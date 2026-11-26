// Jackson OS interim owner console (phase 19). Plain DOM, no innerHTML with data:
// every value from the API is set with textContent, so nothing it returns can run.
"use strict";

const $ = (id) => document.getElementById(id);
const money = (c) => (c < 0 ? "-$" : "$") + (Math.abs(c) / 100).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });
let state = { token: "", tenant: "", clerk: null };

async function bearer() {
  // Clerk session tokens are short-lived; ask Clerk for a fresh one on every call.
  if (state.clerk && state.clerk.session) return state.clerk.session.getToken();
  return state.token;
}

function li(list, text, cls) {
  const el = document.createElement("li");
  el.textContent = text;
  if (cls) el.className = cls;
  list.appendChild(el);
  return el;
}

function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

async function api(path, opts = {}) {
  const r = await fetch(`/tenants/${encodeURIComponent(state.tenant)}${path}`, {
    ...opts,
    headers: { Authorization: `Bearer ${await bearer()}`, "Content-Type": "application/json" },
  });
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

function render(d) {
  $("signin").hidden = true;
  $("dash").hidden = false;
  $("banner").textContent = d.stop.active
    ? `GLOBAL STOP is on — ${d.stop.reason || ""}` : "Running normally.";
  $("banner").className = d.stop.active ? "critical" : "";
  $("stop").hidden = d.stop.active;
  $("resume").hidden = !d.stop.active;

  const al = $("alerts"); clear(al);
  if (!d.alerts.length) li(al, "No alerts.");
  d.alerts.forEach((a) => li(al, a.message, a.level));

  const tb = $("pnl").tBodies[0]; clear(tb);
  [...d.portfolio.businesses, { business: "Total", ...d.portfolio.total }].forEach((b) => {
    const tr = tb.insertRow();
    [b.business, money(b.actual_revenue_cents), money(b.refunds_cents), money(b.spend_cents),
      money(b.model_cost_cents), money(b.actual_profit_cents), money(b.held_cents),
      money(b.estimated_revenue_cents)].forEach((v) => { tr.insertCell().textContent = v; });
  });

  const ap = $("approvals"); clear(ap);
  if (!d.approvals.items.length) li(ap, "Nothing waiting for you.");
  d.approvals.items.forEach((a) => {
    const el = li(ap, `${a.category}: ${a.description}` +
      (a.amount_cents ? ` (${money(a.amount_cents)})` : "") + " ");
    [["Approve", "approve", ""], ["Reject", "reject", "secondary"]].forEach(([label, dec, cls]) => {
      const b = document.createElement("button");
      b.textContent = label; b.className = cls;
      b.addEventListener("click", async () => {
        await api(`/approvals/${a.id}/decision`, { method: "POST",
          body: JSON.stringify({ decision: dec }) });
        load();
      });
      el.appendChild(b);
    });
  });

  const m = $("metrics"); clear(m);
  const pct = (x) => (x === null ? "no data" : `${Math.round(x * 1000) / 10}%`);
  [["Verified success rate", pct(d.metrics.verified_success_rate)],
    ["Failure rate", pct(d.metrics.failure_rate)],
    ["Cost per run", d.metrics.cost_per_run_cents === null ? "no data"
      : money(d.metrics.cost_per_run_cents)],
    ["Average run time", d.metrics.avg_latency_seconds === null ? "no data"
      : `${d.metrics.avg_latency_seconds}s`],
    ["Average approval wait", d.metrics.avg_approval_wait_seconds === null ? "no data"
      : `${d.metrics.avg_approval_wait_seconds}s`],
    ["Gross margin", pct(d.metrics.gross_margin_pct)],
    ["Refund rate", pct(d.metrics.refund_rate)],
    ["Conversion / CAC", "not measured: " + d.metrics.not_measured_reason]].forEach(([k, v]) => {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v;
    m.append(dt, dd);
  });

  const runs = $("runs"); clear(runs);
  if (!d.runs.length) li(runs, "No runs yet.");
  d.runs.forEach((r) => li(runs, `${r.created_at.slice(0, 16).replace("T", " ")} · ${r.status}` +
    ` · ${r.objective} · ${money(r.cost_cents)}` +
    (r.model_cost_usd > 0 ? ` (AI $${r.model_cost_usd.toFixed(4)})` : "")));

  const op = $("opps"); clear(op);
  Object.entries(d.opportunities.by_status).forEach(([s, n]) => li(op, `${s}: ${n}`));
  d.opportunities.top.forEach((o) => li(op, `★ ${o.title} — score ${o.score}`));

  const ag = $("agents"); clear(ag);
  if (!d.agents.length) li(ag, "No agents yet.");
  d.agents.forEach((a) => li(ag, `${a.name} v${a.version} (${a.status}) — ` +
    (Object.entries(a.tasks).map(([s, n]) => `${s} ${n}`).join(", ") || "no tasks")));

  const lv = $("live"); clear(lv);
  if (!d.live_visual_work.length) li(lv, "No browser sessions running.");
  d.live_visual_work.forEach((s) => li(lv, `${s.status} · ${s.url || ""} · ${s.last_action || ""}`));

  const ar = $("artifacts"); clear(ar);
  if (!d.artifacts.length) li(ar, "No artifacts yet.");
  d.artifacts.forEach((a) => li(ar, `${a.type || a.kind} · ${a.content_type} · ${a.size_bytes} bytes`));

  const co = $("connectors"); clear(co);
  d.connectors.forEach((c) => li(co, `${c.name}: ${c.mode.toUpperCase()}` +
    (c.needs ? ` — needs ${c.needs}` : ""), c.mode === "mock" ? "mock" : ""));

  const au = $("audit"); clear(au);
  d.audit.forEach((e) => li(au, `${e.at.slice(0, 16).replace("T", " ")} · ${e.action}`));
}

async function load() {
  try {
    $("err").textContent = "";
    render(await api("/dashboard"));
  } catch (e) {
    $("signin").hidden = false; $("dash").hidden = true;
    $("err").textContent = `Couldn't load the dashboard (${e.message}).`;
  }
}

function loadScript(src, attrs = {}) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src; el.async = true; el.crossOrigin = "anonymous";
    Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
    el.onload = resolve;
    el.onerror = () => reject(new Error(`couldn't load ${src}`));
    document.head.appendChild(el);
  });
}

async function startWithClerk(key) {
  // Per Clerk's JavaScript quickstart: the Frontend API host is encoded in the key.
  const host = atob(key.split("_")[2]).slice(0, -1);
  await loadScript(`https://${host}/npm/@clerk/ui@1/dist/ui.browser.js`);
  await loadScript(`https://${host}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`,
    { "data-clerk-publishable-key": key });
  await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
  state.clerk = window.Clerk;
  $("manual").hidden = true;
  if (!state.clerk.isSignedIn) {
    state.clerk.mountSignIn($("clerk"));
    state.clerk.addListener(({ session }) => { if (session) window.location.reload(); });
    return;
  }
  state.clerk.mountUserButton($("userbtn"));
  const r = await fetch("/me", { headers: { Authorization: `Bearer ${await bearer()}` } });
  if (r.status === 403) {
    $("err").textContent = "You're signed in, but this Jackson OS isn't set up for you yet. " +
      `Put this value in OWNER_AUTH_SUBJECT on the host and redeploy: ${state.clerk.user.id}`;
    return;
  }
  if (!r.ok) { $("err").textContent = `Sign-in check failed (${r.status}).`; return; }
  const me = await r.json();
  const m = me.memberships.find((x) => x.role === "owner") || me.memberships[0];
  if (!m) { $("err").textContent = "Your account isn't a member of any workspace."; return; }
  state.tenant = m.tenant_id;
  load();
}

document.addEventListener("DOMContentLoaded", async () => {
  $("stop").addEventListener("click", async () => {
    const reason = window.prompt("GLOBAL STOP: all running work is cancelled and nothing new " +
      "starts until you resume. Why are you stopping?");
    if (!reason) return;
    const out = await api("/stop", { method: "POST", body: JSON.stringify({ reason }) });
    window.alert(`Stopped. ${out.runs_canceled.length} run(s) cancelled. ` +
      "Check the reconcile list in the audit log.");
    load();
  });
  $("resume").addEventListener("click", async () => {
    await api("/resume", { method: "POST" });
    load();
  });
  $("go").addEventListener("click", () => {
    state.token = $("token").value.trim();
    state.tenant = $("tenant").value.trim();
    try {
      sessionStorage.setItem("jackson.token", state.token);
      sessionStorage.setItem("jackson.tenant", state.tenant);
    } catch (_) { /* fine: this tab only */ }
    load();
  });
  try {
    const cfg = await (await fetch("/console/config.json")).json();
    if (cfg.clerk_publishable_key) {
      startWithClerk(cfg.clerk_publishable_key).catch((e) => {
        $("err").textContent = `Sign-in couldn't start (${e.message}).`;
      });
      return;
    }
  } catch (_) { /* fall back to a pasted token */ }
  try {
    state.token = sessionStorage.getItem("jackson.token") || "";
    state.tenant = sessionStorage.getItem("jackson.tenant") || "";
  } catch (_) { /* storage blocked: ask again */ }
  if (state.token && state.tenant) load();
});
