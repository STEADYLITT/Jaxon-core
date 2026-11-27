// Same sign-in as the console: Clerk when configured, otherwise a pasted token (dev/tests).
import type { CityEvent, Snapshot } from "./types";

interface ClerkLike {
  load(o: Record<string, unknown>): Promise<void>;
  isSignedIn: boolean;
  session: { getToken(): Promise<string> } | null;
  user: { id: string } | null;
  mountSignIn(el: HTMLElement, o: Record<string, unknown>): void;
  mountUserButton(el: HTMLElement): void;
  addListener(cb: (e: { session: unknown }) => void): void;
}
declare global {
  interface Window { Clerk?: ClerkLike; __internal_ClerkUICtor?: unknown }
}

export const auth = { token: "", tenant: "", clerk: null as ClerkLike | null, eventsTimeout: 25 };

export async function bearer(): Promise<string> {
  if (auth.clerk?.session) return auth.clerk.session.getToken();
  return auth.token;
}

export async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const r = await fetch(`/tenants/${encodeURIComponent(auth.tenant)}${path}`, {
    ...opts,
    headers: { Authorization: `Bearer ${await bearer()}`, "Content-Type": "application/json" },
  });
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json() as Promise<T>;
}

export const getState = () => api<Snapshot>("/city/state");

function loadScript(src: string, attrs: Record<string, string> = {}): Promise<void> {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src; el.async = true; el.crossOrigin = "anonymous";
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`couldn't load ${src}`));
    document.head.appendChild(el);
  });
}

export type SignIn =
  | { kind: "ready" } | { kind: "manual" } | { kind: "clerk-signin" } | { kind: "error"; message: string };

export async function startAuth(signInEl: () => HTMLElement | null): Promise<SignIn> {
  let key: string | null = null;
  try {
    const cfg = await (await fetch("/console/config.json")).json();
    if (cfg.events_timeout) auth.eventsTimeout = cfg.events_timeout;
    key = cfg.clerk_publishable_key;
  } catch { /* fall back to a pasted token */ }
  if (!key) {
    try {
      auth.token = sessionStorage.getItem("jackson.token") || "";
      auth.tenant = sessionStorage.getItem("jackson.tenant") || "";
    } catch { /* storage blocked */ }
    return auth.token && auth.tenant ? { kind: "ready" } : { kind: "manual" };
  }
  const host = atob(key.split("_")[2] ?? "").slice(0, -1);
  await loadScript(`https://${host}/npm/@clerk/ui@1/dist/ui.browser.js`);
  await loadScript(`https://${host}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`,
    { "data-clerk-publishable-key": key });
  const clerk = window.Clerk!;
  await clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor },
    signInForceRedirectUrl: "/city", signUpForceRedirectUrl: "/city", afterSignOutUrl: "/city" });
  auth.clerk = clerk;
  if (!clerk.isSignedIn) {
    const el = signInEl();
    if (el) clerk.mountSignIn(el, { forceRedirectUrl: "/city", signUpForceRedirectUrl: "/city" });
    clerk.addListener(({ session }) => { if (session) window.location.reload(); });
    return { kind: "clerk-signin" };
  }
  const r = await fetch("/me", { headers: { Authorization: `Bearer ${await bearer()}` } });
  if (r.status === 403) {
    return { kind: "error", message: "You're signed in, but this Jackson OS isn't set up for " +
      `you yet. Put this value in OWNER_AUTH_SUBJECT and redeploy: ${clerk.user?.id}` };
  }
  if (!r.ok) return { kind: "error", message: `Sign-in check failed (${r.status}).` };
  const me = await r.json() as { memberships: { tenant_id: string; role: string }[] };
  const m = me.memberships.find((x) => x.role === "owner") ?? me.memberships[0];
  if (!m) return { kind: "error", message: "Your account isn't a member of any workspace." };
  auth.tenant = m.tenant_id;
  return { kind: "ready" };
}

export function manualSignIn(token: string, tenant: string): void {
  auth.token = token.trim(); auth.tenant = tenant.trim();
  try {
    sessionStorage.setItem("jackson.token", auth.token);
    sessionStorage.setItem("jackson.tenant", auth.tenant);
  } catch { /* this tab only */ }
}

/** Parse one SSE chunk stream into events. Exported for tests. */
export function parseSse(buffer: string): { events: CityEvent[]; rest: string } {
  const events: CityEvent[] = [];
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const block of parts) {
    const data = block.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6));
    if (data.length) {
      try { events.push(JSON.parse(data.join("\n")) as CityEvent); } catch { /* skip */ }
    }
  }
  return { events, rest };
}

/** Follow /city/stream forever (reconnecting), handing each real event to `onEvent`. */
export async function follow(after: () => number, onEvent: (e: CityEvent) => void,
  stop: () => boolean): Promise<void> {
  while (!stop()) {
    try {
      const r = await fetch(`/tenants/${encodeURIComponent(auth.tenant)}/city/stream?after=` +
        `${after()}&timeout=${auth.eventsTimeout}`,
        { headers: { Authorization: `Bearer ${await bearer()}` } });
      if (!r.ok || !r.body) throw new Error(String(r.status));
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        const parsed = parseSse(buf + dec.decode(value, { stream: true }));
        buf = parsed.rest;
        parsed.events.forEach(onEvent);
      }
    } catch {
      await new Promise((res) => setTimeout(res, 2000));
    }
  }
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

/** Client view: the signed link is the only credential; read-only, no stream. */
export async function getSharedState(token: string): Promise<Snapshot> {
  const r = await fetch(`/city/share/${encodeURIComponent(token)}/state`);
  if (!r.ok) throw new Error(r.status === 404 ? "This link is not valid or has expired." : String(r.status));
  return r.json() as Promise<Snapshot>;
}
