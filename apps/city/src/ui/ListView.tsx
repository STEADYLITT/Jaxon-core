import { describe } from "../lib/effects";
import { windowState } from "../lib/layout";
import { useCity } from "../lib/store";

const LABEL: Record<string, string> = {
  working: "working", just_done: "just finished", done: "done", waiting: "waiting for approval",
  failed: "failed", offline: "offline", paused: "paused", killed: "killed", idle: "idle",
};

/** The same live data as the 3D city, as a plain list (phones, no WebGL, or by choice). */
export function ListView() {
  const s = useCity((st) => st.snapshot);
  const setFocus = useCity((st) => st.setFocus);
  const ticker = useCity((st) => st.ticker);
  if (!s) return null;
  const now = Date.now();
  const jaxon = s.agents.filter((a) => !a.department_id);
  return (
    <main className="list" aria-label="City as a list">
      <section>
        <h2>Steady's inbox <button className="link" id="open-inbox" onClick={() => setFocus({ kind: "ceo" })}>open</button></h2>
        {s.inbox.approvals.length === 0 ? <p className="muted">Nothing waiting for you.</p> : (
          <ul>{s.inbox.approvals.map((a) => (
            <li key={a.id} className="sealed">{a.question}{a.amount_cents ? ` ($${(a.amount_cents / 100).toFixed(2)})` : ""}</li>
          ))}</ul>
        )}
      </section>
      <section>
        <h2>Jaxon <button className="link" onClick={() => setFocus({ kind: "jaxon" })}>routing</button></h2>
        <p className="muted">{s.jaxon.events_per_minute} events/min</p>
        <ul>{jaxon.map((a) => <li key={a.id} data-state={windowState(a, now)}>{a.name} · {LABEL[windowState(a, now)]}</li>)}</ul>
      </section>
      {s.departments.map((d) => (
        <section key={d.id}>
          <h2 style={{ color: d.color }}>{d.name}{d.staffed ? "" : " — not staffed"}</h2>
          {d.floors.filter((f) => f.agents.length).map((f) => (
            <div key={f.id}>
              <h3>{f.name}</h3>
              <ul>{f.agents.map((id) => s.agents.find((a) => a.id === id)).filter((a) => !!a).map((a) => (
                <li key={a!.id} data-state={windowState(a!, now)}>
                  <span className="dot" /><button className="link agent-link" data-agent={a!.id}
                    onClick={() => setFocus({ kind: "agent", id: a!.id })}>{a!.name}</button> · {LABEL[windowState(a!, now)]}
                  {a!.current_task ? ` — ${a!.current_task.title}` : ""}
                </li>
              ))}</ul>
            </div>
          ))}
        </section>
      ))}
      <section>
        <h2>Live feed</h2>
        <ol className="feed">{ticker.map((e) => <li key={e.id}>{describe(e)}</li>)}</ol>
      </section>
    </main>
  );
}
