import { useCity } from "../lib/store";

const usd = (n: number) => `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function Hud() {
  const s = useCity((st) => st.snapshot);
  if (!s) return null;
  const h = s.hud;
  const items: [string, string, string][] = [
    ["online", "Agents online", `${h.agents_online}/${h.agents_total}`],
    ["flight", "Tasks in flight", String(h.tasks_in_flight)],
    ["done", "Done today", String(h.done_today)],
    ["failed", "Failed today", String(h.failed_today)],
    ["spend", "Spend today", usd(h.spend_today_usd)],
  ];
  if (h.revenue_today_usd !== null) items.push(["revenue", "Revenue today", usd(h.revenue_today_usd)]);
  return (
    <header className="hud" aria-label="City status">
      {items.map(([k, label, v]) => (
        <div key={k} className={`stat ${k}`} data-stat={k}>
          <span className="v">{v}</span><span className="k">{label}</span>
        </div>
      ))}
    </header>
  );
}
