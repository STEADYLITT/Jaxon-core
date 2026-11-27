// Side panels: a building's floors, one agent (with real controls), Jaxon's routing brain,
// and Steady's inbox in the CEO tower. Everything is fetched from the API; buttons call the
// real endpoints and the panel refreshes from the database afterwards.
import { useEffect, useMemo, useState } from "react";

import { api, post } from "../lib/api";
import { describe } from "../lib/effects";
import { windowState } from "../lib/layout";
import { useCity, type Focus } from "../lib/store";
import type { AgentDetail, CourseSummary, JaxonView, LineView, TraceView } from "../lib/types";

const money = (c: number) => `$${(c / 100).toFixed(2)}`;
const STATE: Record<string, string> = {
  working: "working", just_done: "just finished", done: "done", waiting: "waiting for approval",
  failed: "failed", offline: "offline", paused: "paused", killed: "killed", idle: "idle",
};

function useEvery<T>(load: () => Promise<T>, deps: unknown[], ms = 2500): [T | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const lastEvent = useCity((st) => st.lastEventId);
  const reload = () => { load().then(setData).catch(() => undefined); };
  useEffect(() => {
    reload();
    const t = window.setInterval(reload, ms);
    return () => window.clearInterval(t);
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, [lastEvent]); // eslint-disable-line react-hooks/exhaustive-deps
  return [data, reload];
}

function Shell({ title, onClose, children, label }: {
  title: string; onClose: () => void; children: React.ReactNode; label: string;
}) {
  return (
    <aside className="panel-side" aria-label={label} data-panel={label}>
      <header>
        <h2>{title}</h2>
        <button className="close" onClick={onClose} aria-label="Close">×</button>
      </header>
      <div className="panel-body">{children}</div>
    </aside>
  );
}

function fmtKpi(k: LineView["kpis"][number]): string {
  if (k.value === null) return `not measured: ${k.note ?? "no data source yet"}`;
  if (k.unit === "USD") return `$${k.value.toFixed(2)}`;
  return k.unit ? `${k.value} ${k.unit}` : String(k.value);
}

/** The business line behind a building: real KPIs, connectors, agents, playbook, launch. */
function LineDetail({ lineKey }: { lineKey: string }) {
  const [v] = useEvery(() => api<LineView>(`/lines/${lineKey}`), [lineKey], 10_000);
  if (!v) return <p className="muted">Loading the line...</p>;
  return (
    <div className="line-detail" data-line={v.key}>
      <p>{v.summary}</p>
      {v.businesses.length > 0 && <p className="muted">Business: {v.businesses.map((b) => b.name).join(", ")}</p>}
      <h3>KPIs (real data only)</h3>
      <dl className="kpis">{v.kpis.map((k) => (
        <div key={k.name} data-measured={k.value !== null}><dt>{k.name}</dt><dd>{fmtKpi(k)}</dd></div>
      ))}</dl>
      <h3>Connections</h3>
      <ul className="conn">{v.connectors.map((c) => (
        <li key={c.id}><span className={`badge ${c.mode.toLowerCase()}`}>{c.mode}</span> {c.name}
          {c.missing.length > 0 && <span className="muted"> (needs {c.missing.join(" + ")})</span>}</li>
      ))}</ul>
      {v.proposed_agents.length > 0 && (<>
        <h3>Proposed agents (waiting for your approval)</h3>
        <ul className="proposed">{v.proposed_agents.map((p) => <li key={p.id}><strong>{p.name}</strong>: {p.role}</li>)}</ul>
      </>)}
      <h3>Playbook v{v.playbook.version}</h3>
      <ol>{(v.playbook.steps ?? []).map((x, i) => <li key={i}>{x}</li>)}</ol>
      <ul className="rules">{(v.playbook.rules ?? []).map((x, i) => <li key={i}>{x}</li>)}</ul>
      <h3>Budget</h3>
      <p>${v.budget.spent_this_month_usd.toFixed(2)} spent this month of ${(v.budget.monthly_cents / 100).toFixed(2)}</p>
      <h3>Launches</h3>
      <p className="muted">{v.launch.how}</p>
      <p>{v.launch.active} active</p>
      {v.key === "courses" && <CourseList />}
    </div>
  );
}

function fmtPrice(p: string | null): string {
  return p ? "$" + p : "not set";
}

/** Course Factory builds (ADR-040): what was drafted, whether its pages passed their checks,
 *  and the launch waiting for Steady's approval. */
function CourseList() {
  const [list] = useEvery(() => api<CourseSummary[]>("/courses"), [], 10_000);
  if (!list) return null;
  return (
    <div className="courses">
      <h3>Courses built</h3>
      {list.length === 0 && <p className="muted">None yet. Try: "AI trading course is trending, build it".</p>}
      <ul>{list.map((c) => (
        <li key={c.id} data-course={c.id}>
          <strong>{c.title}</strong>{c.mode === "mock" && <span className="badge mock"> MOCK</span>}
          <br /><span className="muted">{c.modules} modules · {c.lessons} lessons · price {fmtPrice(c.price)}</span>
          <br />Student platform: {c.platform_passed ? "passed its checks" : "needs fixes"} · Sales page: {c.sales_passed ? "passed its checks" : "needs fixes"}
          {c.launch && <><br />Launch: <strong>{c.launch.status === "proposed" ? "waiting for your approval" : c.launch.status}</strong></>}
        </li>
      ))}</ul>
    </div>
  );
}

function BuildingPanel({ id }: { id: string }) {
  const s = useCity((st) => st.snapshot);
  const share = useCity((st) => st.shareToken);
  const setFocus = useCity((st) => st.setFocus);
  const d = s?.departments.find((x) => x.id === id);
  if (!s || !d) return null;
  return (
    <Shell title={d.name} label="building" onClose={() => setFocus(null)}>
      {!d.staffed && <p className="muted">Not staffed: no active agent does this work yet.</p>}
      <ol className="floors">
        {d.floors.map((f) => {
          const agents = f.agents.map((aid) => s.agents.find((a) => a.id === aid)).filter((a) => !!a);
          return (
            <li key={f.id}>
              <button className="floor" disabled={!agents.length}
                onClick={() => setFocus({ kind: "floor", deptId: d.id, floorId: f.id })}>
                <span>Floor {f.level} · {f.name}</span>
                <span className="muted">{agents.length ? `${agents.length} agent${agents.length > 1 ? "s" : ""}` : "empty"}</span>
              </button>
              <ul className="mini">{agents.map((a) => (
                <li key={a!.id} data-state={windowState(a!)}><span className="dot" />{a!.name} · {STATE[windowState(a!)]}</li>
              ))}</ul>
            </li>
          );
        })}
      </ol>
      {!share && <LineDetail lineKey={d.key} />}
    </Shell>
  );
}

export function AgentPanel({ id }: { id: string }) {
  const setFocus = useCity((st) => st.setFocus);
  const focus = useCity((st) => st.focus);
  const all = useCity((st) => st.snapshot?.agents); // stable reference; derive below
  const others = useMemo(() => (all ?? []).filter((a) => a.key.startsWith("spec:") && a.id !== id),
    [all, id]);
  const [d, reload] = useEvery(() => api<AgentDetail>(`/city/agents/${id}`), [id]);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [to, setTo] = useState("");
  const back = focus && focus.kind === "agent" ? (focus.from ?? null) : null;
  async function act(action: string, body?: unknown) {
    if (action === "kill" && !window.confirm("Kill this agent? Its running task is stopped now.")) return;
    setBusy(action); setMsg("");
    try {
      const r = await post<{ status: string; pending_stop: boolean }>(`/city/agents/${id}/${action}`, body);
      setMsg(r.pending_stop ? "Stopping its process…" : `Now ${r.status}.`);
    } catch (e) { setMsg((e as Error).message); }
    setBusy(""); reload();
  }
  if (!d) return <Shell title="Agent" label="agent" onClose={() => setFocus(back)}><p className="muted">Loading…</p></Shell>;
  const st = windowState(d);
  return (
    <Shell title={d.name} label="agent" onClose={() => setFocus(back)}>
      <p className="role">{d.role}</p>
      <p data-state={st} className="state"><span className="dot" />{STATE[st]}{d.model ? ` · ${d.model}` : ""}</p>
      <dl className="stats">
        <div><dt>Cost today</dt><dd>${d.cost_today_usd.toFixed(4)}</dd></div>
        <div><dt>Done today</dt><dd>{d.done_today}</dd></div>
        <div><dt>Failed today</dt><dd>{d.failed_today}</dd></div>
        <div><dt>Success (7d)</dt><dd>{d.success_rate_7d === null ? "no data" : `${Math.round(d.success_rate_7d * 100)}%`}</dd></div>
      </dl>
      <section>
        <h3>Current task</h3>
        {d.current_task ? (
          <>
            <p>{d.current_task.title}</p>
            {d.time_on_task_s !== null && <p className="muted">on it for {d.time_on_task_s}s</p>}
            <ol className="trace" aria-label="Trace back to Steady's command">
              {d.trace.map((t, i) => <li key={i}>{t.command ? `Steady: “${t.command}”` : `${t.title} (${t.status})`}</li>)}
            </ol>
          </>
        ) : <p className="muted">No task right now.</p>}
      </section>
      <div className="controls">
        <button disabled={!d.controls.pause || !!busy} onClick={() => act("pause")}>Pause</button>
        <button disabled={!d.controls.resume || !!busy} onClick={() => act("resume")}>Resume</button>
        <button className="danger" disabled={!d.controls.kill || !!busy} onClick={() => act("kill")}>Kill</button>
      </div>
      {d.controls.reassign && (
        <div className="controls">
          <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Reassign to">
            <option value="">Reassign to…</option>
            {others.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          <button disabled={!to || !!busy} onClick={() => act("reassign", { to_agent_id: to })}>Reassign</button>
        </div>
      )}
      {msg && <p className="msg" role="status">{msg}</p>}
      <LimitEditor id={id} limit={d.daily_limit_usd ?? 0} spent={d.cost_today_usd} onSaved={reload} />
      <section>
        <h3>Live log</h3>
        {d.log.length ? <ol className="log">{d.log.map((e) => <li key={e.id}>{String(e.payload["line"] ?? "")}</li>)}</ol>
          : <p className="muted">No log lines yet.</p>}
      </section>
      <section>
        <h3>Tools called</h3>
        {d.tools.length ? <ul className="log">{d.tools.map((e) => <li key={e.id}>{describe(e)}</li>)}</ul>
          : <p className="muted">None yet.</p>}
      </section>
    </Shell>
  );
}

function LimitEditor({ id, limit, spent, onSaved }: {
  id: string; limit: number; spent: number; onSaved: () => void;
}) {
  const [v, setV] = useState(String(limit));
  const [msg, setMsg] = useState("");
  useEffect(() => { setV(String(limit)); }, [limit]);
  async function save() {
    setMsg("");
    try {
      await api(`/city/agents/${id}`, { method: "PATCH", body: JSON.stringify({ daily_limit_usd: Number(v) }) });
      setMsg("Saved."); onSaved();
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <section>
      <h3>Cost guard</h3>
      <p className="muted">Spent ${spent.toFixed(2)} of ${limit.toFixed(2)} today. Over the limit it pauses and sends you a red letter.</p>
      <div className="controls">
        <label className="limit">Daily limit $ <input type="number" min={0} step={0.5} value={v}
          onChange={(e) => setV(e.target.value)} aria-label="Daily limit in dollars" /></label>
        <button onClick={save}>Save</button>
      </div>
      {msg && <p className="msg" role="status">{msg}</p>}
    </section>
  );
}

function TracePanel({ runId }: { runId: string }) {
  const setFocus = useCity((st) => st.setFocus);
  const [t] = useEvery(() => api<TraceView>(`/city/trace/${runId}`), [runId], 4000);
  return (
    <Shell title="Trace" label="trace" onClose={() => setFocus(null)}>
      {!t ? <p className="muted">Loading…</p> : (
        <ol className="trace-steps">
          <li><strong>Steady:</strong> “{t.command.text}” <span className="muted">· {t.command.status}</span></li>
          {t.routing.map((r) => (
            <li key={r.id}><strong>Jaxon routed it</strong> → {String(r.payload["agent"])}: {String(r.payload["reason"])}</li>
          ))}
          {t.tasks.map((x) => (
            <li key={x.task_id}>
              <button className="link" onClick={() => setFocus({ kind: "agent", id: x.agent_id, from: { kind: "trace", runId } })}>
                {x.agent}</button>: {x.title} <span className="muted">({x.status})</span>
              {x.error && <p className="err">{x.error}</p>}
              {x.result && <p className="muted">{Object.entries(x.result).slice(0, 4).map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}</p>}
            </li>
          ))}
          {t.approvals.map((a) => <li key={a.id}>{describe(a)}</li>)}
          <li className="muted">Cost of this command: ${t.cost_usd.toFixed(4)}</li>
        </ol>
      )}
    </Shell>
  );
}

function ClientLinks() {
  const [biz, setBiz] = useState<{ id: string; name: string }[]>([]);
  const [shares, setShares] = useState<{ id: string; label: string; expires_at: string; revoked: boolean }[]>([]);
  const [pick, setPick] = useState("");
  const [made, setMade] = useState("");
  const load = () => {
    api<{ id: string; name: string }[]>("/businesses").then(setBiz).catch(() => undefined);
    api<typeof shares>("/city/shares").then(setShares).catch(() => undefined);
  };
  useEffect(load, []);
  async function create() {
    const r = await post<{ url: string }>("/city/shares", { business_id: pick, days: 7 });
    setMade(r.url); load();
  }
  return (
    <section>
      <h3>Client view links</h3>
      <p className="muted">A read-only city showing only that client's work. Expires in 7 days; revoke any time.</p>
      <div className="controls">
        <select value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Client">
          <option value="">Choose a client…</option>
          {biz.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <button disabled={!pick} onClick={create}>Create link</button>
      </div>
      {made && <p className="share-url"><input readOnly value={made} aria-label="Share link" onFocus={(e) => e.currentTarget.select()} /></p>}
      <ul>{shares.map((x) => (
        <li key={x.id}>{x.label} · {x.revoked ? "revoked" : `until ${new Date(x.expires_at).toLocaleDateString()}`}
          {!x.revoked && <button className="link" onClick={async () => { await post(`/city/shares/${x.id}/revoke`); load(); }}> revoke</button>}
        </li>
      ))}</ul>
    </section>
  );
}

function JaxonPanel() {
  const setFocus = useCity((st) => st.setFocus);
  const [v] = useEvery(() => api<JaxonView>("/city/jaxon"), []);
  return (
    <Shell title="Jaxon · routing" label="jaxon" onClose={() => setFocus(null)}>
      {!v ? <p className="muted">Loading…</p> : !v.commands.length
        ? <p className="muted">No commands yet. Type one in the command bar.</p> : (
        <ol className="commands">{v.commands.map((c) => (
          <li key={c.run_id}>
            <p><strong>“{c.command}”</strong> <span className="muted">· {c.status}</span></p>
            <ul>{c.routed_to.map((r, i) => (
              <li key={i}><button className="link" onClick={() => setFocus({ kind: "agent", id: r.agent_id, from: { kind: "jaxon" } })}>
                {r.department} → {r.agent}</button>: {r.task} <span className="muted">({r.status})</span></li>
            ))}</ul>
            {c.routing_reasons.map((e) => <p key={e.id} className="muted">why: {String(e.payload["reason"] ?? "")}</p>)}
            {c.memory.filter((m) => m.offered !== null).map((m, i) => (
              <p key={i} className="muted">memory: {m.offered} looked up, {(m.cited ?? []).length} cited</p>
            ))}
          </li>
        ))}</ol>
      )}
    </Shell>
  );
}

export function InboxPanel() {
  const s = useCity((st) => st.snapshot);
  const setFocus = useCity((st) => st.setFocus);
  const [msg, setMsg] = useState("");
  async function decide(id: string, decision: "approve" | "reject") {
    setMsg("");
    try {
      await post(`/approvals/${id}/decision`, { decision });
      setMsg(decision === "approve" ? "Approved: the agent continues." : "Denied: the task stops.");
    } catch (e) { setMsg((e as Error).message); }
  }
  if (!s) return null;
  return (
    <Shell title="Steady's inbox" label="inbox" onClose={() => setFocus(null)}>
      <section>
        <h3>Approvals</h3>
        {!s.inbox.approvals.length ? <p className="muted">Nothing waiting for you.</p> : (
          <ul className="approvals">{s.inbox.approvals.map((a) => (
            <li key={a.id} className="sealed" data-approval={a.id}>
              <p>{a.question}{a.amount_cents ? <strong> {money(a.amount_cents)}</strong> : null}</p>
              <p className="muted">{a.reason}</p>
              <div className="controls">
                <button onClick={() => decide(a.id, "approve")}>Approve</button>
                <button className="danger" onClick={() => decide(a.id, "reject")}>Deny</button>
              </div>
            </li>
          ))}</ul>
        )}
      </section>
      {msg && <p className="msg" role="status">{msg}</p>}
      <section>
        <h3>Red letters</h3>
        {!(s.inbox.letters ?? []).length ? <p className="muted">None in the last 24 hours.</p> : (
          <ul className="approvals">{(s.inbox.letters ?? []).map((e) => (
            <li key={e.id} className="sealed letter" data-letter={String(e.payload["kind"])}>
              <p>{String(e.payload["message"] ?? "")}</p>
              <p className="muted">{new Date(e.ts).toLocaleTimeString()}</p>
            </li>
          ))}</ul>
        )}
      </section>
      <section>
        <h3>Alerts</h3>
        {!s.inbox.alerts.length ? <p className="muted">No alerts.</p> : (
          <ul>{s.inbox.alerts.map((a, i) => <li key={i} data-level={a.level}>{a.message ?? a.kind}</li>)}</ul>
        )}
      </section>
      <ClientLinks />
    </Shell>
  );
}

function FloorPanel({ deptId, floorId }: { deptId: string; floorId: string }) {
  const s = useCity((st) => st.snapshot);
  const setFocus = useCity((st) => st.setFocus);
  const d = s?.departments.find((x) => x.id === deptId);
  const f = d?.floors.find((x) => x.id === floorId);
  if (!d || !f) return null;
  return (
    <div className="floor-bar">
      <button className="link" onClick={() => setFocus({ kind: "building", id: deptId })}>← {d.name}</button>
      <span>Floor {f.level} · {f.name}</span>
      <span className="muted">tap a robot for its controls</span>
    </div>
  );
}

export function Panels() {
  const focus: Focus = useCity((st) => st.focus);
  const share = useCity((st) => st.shareToken);
  if (!focus) return null;
  if (share && !["building", "floor"].includes(focus.kind)) return null; // client view: read-only
  switch (focus.kind) {
    case "building": return <BuildingPanel id={focus.id} />;
    case "floor": return <FloorPanel deptId={focus.deptId} floorId={focus.floorId} />;
    case "agent": return <AgentPanel id={focus.id} />;
    case "jaxon": return <JaxonPanel />;
    case "ceo": return <InboxPanel />;
    case "trace": return <TracePanel runId={focus.runId} />;
    default: return null;
  }
}
