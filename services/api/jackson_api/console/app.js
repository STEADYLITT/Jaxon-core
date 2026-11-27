// Jackson OS owner console: the command center (phase 19, redesigned in phase 22).
// Plain DOM, no innerHTML with data: every value from the API is set with textContent,
// so nothing it returns can run. Shows only real records; empty states otherwise.
"use strict";

const $ = (id) => document.getElementById(id);
const money = (c) => (c < 0 ? "-$" : "$") + (Math.abs(c) / 100).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });
let state = { token: "", tenant: "", clerk: null, me: null, feedStarted: false,
  eventsTimeout: 25 };
// What the 3D core draws (read every frame by core.js). Only real records go in here.
window.__jackson = { state: { agents: [], thinking: false, stopped: false } };
let lastDash = null;

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
  lastDash = d;
  greet(d);
  coreStatus(d);
  businesses(d.portfolio);

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
  d.opportunities.top.forEach((o) => {
    const el = li(op, `★ ${o.title} — score ${o.score} `);
    const b = document.createElement("button");
    b.textContent = "Launch"; b.className = "launch";
    b.addEventListener("click", () => openLaunch(o, d.portfolio.businesses));
    el.appendChild(b);
  });
  if (!d.opportunities.top.length) li(op, "No opportunities yet.");

  const la = $("launches"); clear(la);
  if (!d.launches.length) li(la, "No launches yet. Use Launch on an opportunity.");
  d.launches.forEach((x) => {
    const el = li(la, `${x.title} · ${x.status}` +
      (x.status_reason ? ` (${x.status_reason})` : "") +
      ` · spent ${money(x.spend_cents)} of ${money(x.total_budget_cents)}` +
      ` · cap ${money(x.daily_cap_cents)}/day · ${x.sales} sale(s) `,
      ["paused", "killed"].includes(x.status) ? "warning" : "");
    const acts = x.status === "active" ? ["pause", "revoke"]
      : ["paused", "killed", "completed"].includes(x.status) ? ["resume", "revoke"] : [];
    acts.forEach((a) => {
      const b = document.createElement("button");
      b.textContent = a[0].toUpperCase() + a.slice(1); b.className = "secondary";
      b.addEventListener("click", async () => {
        await api(`/launches/${x.id}/${a}`, { method: "POST" }); load();
      });
      el.appendChild(b);
    });
  });

  const ag = $("agents"); clear(ag);
  if (!d.agents.length) li(ag, "No agents yet. Jackson creates them as work needs them.");
  d.agents.forEach((a) => {
    const item = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.agent = a.id;
    const st = document.createElement("span");
    st.className = `st st-${a.state}`; st.textContent = STATE_LABEL[a.state] || a.state;
    const name = document.createElement("strong"); name.textContent = `${a.name} v${a.version}`;
    const task = document.createElement("div");
    task.textContent = a.current_task ? a.current_task : "No task yet";
    const cost = document.createElement("div");
    cost.textContent = `Cost today $${Number(a.cost_today_usd || 0).toFixed(4)}`;
    b.append(st, name, task, cost);
    b.addEventListener("click", () => showAgent(a.id));
    item.appendChild(b);
    ag.appendChild(item);
  });
  window.__jackson.state = {
    agents: d.agents.map((a) => ({ id: a.id, name: a.name, state: a.state })),
    thinking: d.activity.active_runs > 0, stopped: d.stop.active,
  };
  if (!state.feedStarted) { state.feedStarted = true; feed(d.activity.last_event_id); }

  const lv = $("live"); clear(lv);
  if (!d.live_visual_work.length) li(lv, "No browser sessions running.");
  d.live_visual_work.forEach((s) => li(lv, `${s.status} · ${s.url || ""} · ${s.last_action || ""}`));

  const ar = $("artifacts"); clear(ar);
  if (!d.artifacts.length) li(ar, "No artifacts yet.");
  d.artifacts.forEach((a) => li(ar, `${a.type || a.kind} · ${a.content_type} · ${a.size_bytes} bytes`));

  const co = $("connectors"); clear(co);
  d.connectors.forEach((c) => li(co, `${c.name}: ${c.mode.toUpperCase()}` +
    (c.needs ? ` — needs ${c.needs}` : "") + (c.setup ? ` — ${c.setup}` : ""),
    c.mode === "mock" ? "mock" : ""));

  const au = $("audit"); clear(au);
  d.audit.forEach((e) => li(au, `${e.at.slice(0, 16).replace("T", " ")} · ${e.action}`));
}

const STATE_LABEL = { idle: "idle", running: "running", waiting_approval: "waiting approval",
  error: "error" };

function greet(d) {
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  const name = (state.me && state.me.display_name) || "Steady";
  const n = d.approvals.pending + d.launches.filter((x) => x.status === "proposed").length +
    d.alerts.filter((a) => a.level !== "info").length;
  const g = $("greeting"); clear(g);
  g.append(`${part}, ${name} — `);
  const strong = document.createElement("strong");
  strong.textContent = n === 0 ? "nothing needs you right now"
    : `${n} thing${n === 1 ? "" : "s"} need${n === 1 ? "s" : ""} you today`;
  g.appendChild(strong);
  g.append(".");
}

function coreStatus(d) {
  const cs = $("corestatus");
  const active = d.activity.active_runs;
  cs.className = "core-status" + (d.stop.active ? " stopped" : "");
  cs.textContent = d.stop.active ? "STOPPED — nothing new will start"
    : active ? `Thinking — ${active} run${active === 1 ? "" : "s"} in progress`
      : "Standing by";
  document.body.classList.toggle("thinking", !d.stop.active && active > 0);
  document.body.classList.toggle("stopped", d.stop.active);
}

function businesses(p) {
  const box = $("biz"); clear(box);
  const rows = p.businesses.filter((b) => b.business_id);
  if (!rows.length) { box.textContent = "No businesses yet."; return; }
  rows.forEach((b) => {
    const card = document.createElement("article"); card.className = "biz-card";
    const h = document.createElement("h3"); h.textContent = b.business;
    card.appendChild(h);
    [["Revenue", b.actual_revenue_cents - b.refunds_cents, ""],
      ["Spend", b.spend_cents + b.model_cost_cents, ""],
      ["Profit", b.actual_profit_cents, b.actual_profit_cents >= 0 ? "profit" : "loss"]]
      .forEach(([k, v, cls]) => {
        const row = document.createElement("div"); row.className = `num ${cls}`;
        const label = document.createElement("span"); label.textContent = k;
        const val = document.createElement("b"); val.textContent = money(v);
        row.append(label, val); card.appendChild(row);
      });
    if (b.estimated_revenue_cents) {
      const est = document.createElement("div"); est.className = "est";
      est.textContent = `Estimate (not actual): ${money(b.estimated_revenue_cents)}`;
      card.appendChild(est);
    }
    box.appendChild(card);
  });
}

function showAgent(id) {
  const a = lastDash && lastDash.agents.find((x) => x.id === id);
  if (!a) return;
  $("ad-name").textContent = `${a.name} v${a.version}`;
  $("ad-state").textContent = STATE_LABEL[a.state] || a.state;
  $("ad-task").textContent = a.current_task || "No task yet";
  $("ad-cost").textContent = `$${Number(a.cost_today_usd || 0).toFixed(4)}`;
  $("ad-role").textContent = a.role || "";
  $("ad-tasks").textContent = Object.entries(a.tasks).map(([s, n]) => `${s} ${n}`)
    .join(", ") || "none yet";
  $("agentdetail").hidden = false;
  $("agentdetail").focus();
}

// Live feed: the tenant event stream (SSE) read with fetch so the sign-in token can be sent.
async function feed(after) {
  const list = $("feed");
  if (!list.children.length) li(list, "Waiting for activity…", "mock");
  let last = after || 0;
  for (;;) {
    try {
      const r = await fetch(`/tenants/${encodeURIComponent(state.tenant)}/events?after=${last}` +
        `&timeout=${state.eventsTimeout}`,
        { headers: { Authorization: `Bearer ${await bearer()}` } });
      if (!r.ok || !r.body) throw new Error(String(r.status));
      $("feedstate").textContent = "live"; $("feedstate").className = "pill live";
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const id = /^id: (\d+)/m.exec(chunk);
          const data = /^data: (.*)$/m.exec(chunk);
          if (id) last = Number(id[1]);
          if (data) addFeed(JSON.parse(data[1]));
        }
      }
    } catch (_) {
      $("feedstate").textContent = "reconnecting"; $("feedstate").className = "pill";
    }
    await new Promise((res) => { setTimeout(res, 1500); });
  }
}

function addFeed(ev) {
  const list = $("feed");
  if (list.firstChild && list.firstChild.classList.contains("mock")) clear(list);
  const item = document.createElement("li");
  const t = document.createElement("time");
  t.textContent = (ev.created_at || "").slice(11, 19);
  item.append(t, `${ev.type} · run ${String(ev.run_id).slice(0, 8)}`);
  list.insertBefore(item, list.firstChild);
  while (list.children.length > 60) list.removeChild(list.lastChild);
  if (/^(run\.|approval\.|launch\.|policy\.checked)/.test(ev.type)) scheduleReload();
}

let reloadTimer = null;
function scheduleReload() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(load, 1200);
}

// 3D only where it helps: wide screen, WebGL, no reduced-motion or low-power hint.
function pickMode() {
  const wide = window.matchMedia("(min-width: 900px)").matches;
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const lowPower = (navigator.deviceMemory && navigator.deviceMemory <= 2) ||
    (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2);
  let gl = false;
  try { gl = !!document.createElement("canvas").getContext("webgl2"); } catch (_) { gl = false; }
  return wide && !calm && !lowPower && gl ? "3d" : "2d";
}

async function startCore() {
  if (pickMode() !== "3d") { document.body.dataset.mode = "2d"; return; }
  try {
    const mod = await import("/console/core.js");
    mod.start($("core3d"), $("corelabels"), () => window.__jackson.state, showAgent);
    document.body.dataset.mode = "3d";
  } catch (_) {
    document.body.dataset.mode = "2d"; // any failure: the 2D core is always there
  }
}

async function loadMe() {
  try {
    const r = await fetch("/me", { headers: { Authorization: `Bearer ${await bearer()}` } });
    if (r.ok) state.me = await r.json();
  } catch (_) { /* the greeting falls back to the owner's first name */ }
}

function openLaunch(opp, businesses) {
  const sel = $("lf-business"); clear(sel);
  businesses.filter((b) => b.business_id).forEach((b) => {
    const o = document.createElement("option");
    o.value = b.business_id; o.textContent = b.business;
    if (b.business_id === opp.business_id) o.selected = true;
    sel.appendChild(o);
  });
  $("lf-title").value = `Launch: ${opp.title}`.slice(0, 200);
  $("lf-err").textContent = "";
  $("launchform").dataset.opportunity = opp.id;
  $("launchdlg").showModal();
}

const dollars = (id) => Math.round(Number($(id).value || 0) * 100);
const list = (id) => $(id).value.split(",").map((x) => x.trim()).filter(Boolean);

async function submitLaunch(ev) {
  if (ev.submitter && ev.submitter.value === "cancel") return;
  ev.preventDefault();
  const body = {
    title: $("lf-title").value, business_id: $("lf-business").value,
    opportunity_id: $("launchform").dataset.opportunity || null, plan: $("lf-plan").value,
    total_budget_cents: dollars("lf-budget"), daily_cap_cents: dollars("lf-cap"),
    duration_days: Number($("lf-days").value),
    channels: { shopify_publish: $("lf-shopify").checked, ads: $("lf-ads").checked,
      social_accounts: list("lf-social"), email_lists: list("lf-email") },
    kill_rules: {
      max_cac_cents: $("lf-cac").value ? dollars("lf-cac") : null,
      zero_sales_after_days: $("lf-zero").value ? Number($("lf-zero").value) : null },
  };
  try {
    await api("/launches", { method: "POST", body: JSON.stringify(body) });
    $("launchdlg").close();
    load();
  } catch (e) {
    $("lf-err").textContent = `Couldn't approve the launch (${e.message}).`;
  }
}

let coreStarted = false;
async function load() {
  try {
    $("err").textContent = "";
    if (!state.me) await loadMe();
    render(await api("/dashboard"));
    if (!coreStarted) { coreStarted = true; startCore(); }
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
  // Always come back to the console after signing in, up or out (never the bare site root).
  await window.Clerk.load({
    ui: { ClerkUI: window.__internal_ClerkUICtor },
    signInForceRedirectUrl: "/console",
    signUpForceRedirectUrl: "/console",
    afterSignOutUrl: "/console",
  });
  state.clerk = window.Clerk;
  $("manual").hidden = true;
  if (!state.clerk.isSignedIn) {
    state.clerk.mountSignIn($("clerk"), {
      forceRedirectUrl: "/console", signUpForceRedirectUrl: "/console",
    });
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
  state.me = me;
  const m = me.memberships.find((x) => x.role === "owner") || me.memberships[0];
  if (!m) { $("err").textContent = "Your account isn't a member of any workspace."; return; }
  state.tenant = m.tenant_id;
  load();
}

document.addEventListener("DOMContentLoaded", async () => {
  $("launchform").addEventListener("submit", submitLaunch);
  $("ad-close").addEventListener("click", () => { $("agentdetail").hidden = true; });
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
    if (cfg.events_timeout) state.eventsTimeout = cfg.events_timeout;
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
