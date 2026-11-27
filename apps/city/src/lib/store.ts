import { create } from "zustand";

import { admit, effectsFor, type Effect } from "./effects";
import type { CityEvent, Snapshot } from "./types";

export type Focus =
  | { kind: "building"; id: string }
  | { kind: "floor"; deptId: string; floorId: string }
  | { kind: "agent"; id: string; from?: Focus | null }
  | { kind: "jaxon" }
  | { kind: "ceo" }
  | null;

export interface CityState {
  focus: Focus;
  setFocus(f: Focus): void;
  snapshot: Snapshot | null;
  lastEventId: number;
  ticker: CityEvent[];
  active: Effect[];
  queue: Effect[];
  pulseAt: number;
  pulses: number[]; // event times in the last minute, for the orb's glow
  revenueTicks: number;
  listView: boolean;
  setSnapshot(s: Snapshot): void;
  onEvent(e: CityEvent, now?: number): void;
  step(now: number): void;
  toggleList(): void;
}

export const useCity = create<CityState>((set, get) => ({
  focus: null,
  setFocus(f) { set({ focus: f }); },
  snapshot: null,
  lastEventId: 0,
  ticker: [],
  active: [],
  queue: [],
  pulseAt: 0,
  pulses: [],
  revenueTicks: 0,
  listView: false,
  setSnapshot(s) {
    // The stream cursor is set from the first snapshot only; after that only streamed
    // events move it, so no event between a refetch and the stream is ever skipped.
    set((st) => st.snapshot === null
      ? { snapshot: s, lastEventId: s.last_event_id, ticker: s.ticker.slice(0, 20) }
      : { snapshot: s });
  },
  onEvent(e, now = performance.now()) {
    const st = get();
    if (st.ticker.some((t) => t.id === e.id)) return; // already shown
    const fx = st.snapshot ? effectsFor(e, st.snapshot, now) : [];
    set({
      lastEventId: Math.max(st.lastEventId, e.id),
      ticker: [e, ...st.ticker.filter((t) => t.id !== e.id)].slice(0, 20),
      queue: [...st.queue, ...fx],
      pulseAt: now,
      pulses: [...st.pulses.filter((t) => now - t < 60_000), now],
      revenueTicks: st.revenueTicks + (e.type === "revenue.recorded" ? 1 : 0),
    });
  },
  step(now) {
    const st = get();
    if (!st.queue.length && !st.active.length) return;
    const next = admit(st.active, st.queue, now);
    const same = next.queue.length === st.queue.length && next.active.length === st.active.length &&
      next.active.every((e, i) => e === st.active[i]);
    if (!same) set(next);
  },
  toggleList() { set((st) => ({ listView: !st.listView })); },
}));
