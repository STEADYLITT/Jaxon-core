import { describe } from "../lib/effects";
import { useCity } from "../lib/store";

export function Ticker() {
  const ticker = useCity((st) => st.ticker);
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
                {" "}{describe(e)}
              </li>
            ))}
          </ol>
        )}
    </footer>
  );
}
