// Replay: rebuild each agent's status at any moment from the last 24h of real events.
import type { AgentStatus, CityEvent } from "./types";

const STATUS_OF: Record<string, AgentStatus> = {
  "task.started": "working", "task.completed": "done", "task.failed": "failed",
  "approval.requested": "waiting", "agent.paused": "paused", "agent.resumed": "idle",
  "agent.killed": "killed", "agent.offline": "offline",
};

export interface PastStatus { status: AgentStatus; at: string }

/** Agent statuses (and when each began) after applying events[0..upto], in order. */
export function statusesAt(events: CityEvent[], upto: number): Map<string, PastStatus> {
  const out = new Map<string, PastStatus>();
  for (let i = 0; i <= upto && i < events.length; i++) {
    const e = events[i]!;
    if (!e.agent_id) continue;
    if (e.type === "agent.heartbeat" && e.payload["back_online"]) {
      out.set(e.agent_id, { status: "idle", at: e.ts }); continue;
    }
    const st = STATUS_OF[e.type];
    if (st) out.set(e.agent_id, { status: st, at: e.ts });
  }
  return out;
}

/** Index of the last event at or before time t (ms), or -1. */
export function indexAt(events: CityEvent[], t: number): number {
  let lo = 0, hi = events.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (Date.parse(events[mid]!.ts) <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}
