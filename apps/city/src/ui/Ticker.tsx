import { describe } from "../lib/effects";
import { useCity } from "../lib/store";

export function Ticker() {
  const live = useCity((st) => st.ticker);
  const replay = useCity((st) => st.replay);
  const share = useCity((st) => st.shareToken);
  const setFocus = useCity((st) => st.setFocus);
  const ticker = replay
    ? replay.events.slice(Math.max(0, replay.index - 19), replay.index + 1).reverse() : live;
  return (
    <footer className="ticker" aria-label="Last 20 events" aria-live="polite">
      <span className="tag">RING ROAD</span>
      {ticker.length === 0
        ? <span className="empty">No events yet. The city lights up when an agent does real work.</span>
        : (
          <ol>
            {ticker.map((e) => (
              <li key={e.id} data-event={e.type}>
                <time>{new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time>
                {" "}{e.run_id && !share
                  ? <button className="link traceable" onClick={() => setFocus({ kind: "trace", runId: e.run_id! })}
                      title="Trace this back to the command">{describe(e)}</button>
                  : describe(e)}
              </li>
            ))}
          </ol>
        )}
    </footer>
  );
}
