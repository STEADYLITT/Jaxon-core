// Replay the last 24 hours from the events table. Statuses are rebuilt from the events;
// letters and beams fly again as the clock passes them. Nothing is invented.
import { useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import { indexAt } from "../lib/replay";
import { useCity } from "../lib/store";
import type { CityEvent } from "../lib/types";

const DAY = 24 * 3600 * 1000;

async function last24h(): Promise<CityEvent[]> {
  const since = new Date(Date.now() - DAY).toISOString();
  const out: CityEvent[] = [];
  let after = 0;
  for (let page = 0; page < 20; page++) {
    const batch = await api<CityEvent[]>(`/city/events?since=${after}&since_ts=${encodeURIComponent(since)}&limit=1000`);
    out.push(...batch);
    if (batch.length < 1000) break;
    after = batch[batch.length - 1]!.id;
  }
  return out;
}

export function ReplayBar() {
  const replay = useCity((st) => st.replay);
  const setReplay = useCity((st) => st.setReplay);
  const [loading, setLoading] = useState(false);
  const raf = useRef(0);

  useEffect(() => {
    if (!replay?.playing) return;
    let last = performance.now();
    const tick = (now: number) => {
      const r = useCity.getState().replay;
      if (!r || !r.playing) return;
      const clock = Math.min(r.clock + (now - last) * r.speed, Date.now());
      last = now;
      const idx = indexAt(r.events, clock);
      for (let i = r.index + 1; i <= idx; i++) useCity.getState().replayEvent(r.events[i]!);
      const done = clock >= Date.now() || idx >= r.events.length - 1;
      useCity.setState({ replay: { ...r, clock, index: idx, playing: !done && r.playing } });
      if (!done) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [replay?.playing]);

  async function start() {
    setLoading(true);
    try {
      const events = await last24h();
      const first = events.length ? Date.parse(events[0]!.ts) : Date.now() - DAY;
      setReplay({ events, index: -1, playing: true, speed: 120, clock: first - 1000 });
    } finally { setLoading(false); }
  }

  if (!replay) {
    return <button className="replay-open" id="replay" onClick={start} disabled={loading}>
      {loading ? "Loading…" : "Replay 24h"}</button>;
  }
  const t0 = replay.events.length ? Date.parse(replay.events[0]!.ts) : Date.now() - DAY;
  const span = Math.max(Date.now() - t0, 1);
  return (
    <div className="replay-bar" role="group" aria-label="Replay">
      <span className="replay-tag">REPLAY</span>
      <button onClick={() => setReplay({ ...replay, playing: !replay.playing })}
        aria-label={replay.playing ? "Pause replay" : "Play replay"}>{replay.playing ? "❚❚" : "▶"}</button>
      <input type="range" min={0} max={1000} aria-label="Replay time"
        value={Math.round(((replay.clock - t0) / span) * 1000)}
        onChange={(e) => {
          const clock = t0 + (Number(e.target.value) / 1000) * span;
          setReplay({ ...replay, clock, index: indexAt(replay.events, clock), playing: false });
        }} />
      <time>{new Date(replay.clock).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}</time>
      <span className="muted">{replay.events.length} events</span>
      <select value={replay.speed} aria-label="Speed"
        onChange={(e) => setReplay({ ...replay, speed: Number(e.target.value) })}>
        {[30, 120, 600, 3600].map((s) => <option key={s} value={s}>{s}×</option>)}
      </select>
      <button onClick={() => setReplay(null)}>Live</button>
    </div>
  );
}
