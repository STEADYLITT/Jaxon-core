import type { Agent, CityEvent, Snapshot } from "../src/lib/types";

// Test fixture shaped like a real /city/state response (used only by unit tests).
export function agent(over: Partial<Agent> = {}): Agent {
  return {
    id: "a1", name: "Content Writer", role: "drafts", key: "kind:content_generate",
    department_id: "d-mkt", floor_id: "f-content", status: "idle", online: true, model: null,
    last_heartbeat_at: null, status_changed_at: null, current_task: null, cost_today_usd: 0,
    done_today: 0, failed_today: 0, success_rate_7d: null, ...over,
  };
}

export function snapshot(over: Partial<Snapshot> = {}): Snapshot {
  return {
    generated_at: "", demo: false, last_event_id: 10,
    jaxon: { events_per_minute: 0, agents: ["jx"] },
    departments: [
      { id: "d-mkt", key: "marketing", name: "Marketing", color: "#f0f", slot: 0, staffed: true,
        floors: [{ id: "f-content", key: "content", name: "Content", level: 1, agents: ["a1", "a2"] },
                 { id: "f-social", key: "social", name: "Social", level: 2, agents: [] }] },
      { id: "d-sup", key: "support", name: "Support", color: "#f88", slot: 6, staffed: false,
        floors: [{ id: "f-help", key: "helpdesk", name: "Help Desk", level: 1, agents: [] }] },
    ],
    agents: [agent(), agent({ id: "a2", name: "Video Producer", key: "kind:content_video" }),
             agent({ id: "jx", name: "Jaxon Planner", department_id: null, floor_id: null })],
    hud: { agents_online: 3, agents_total: 3, tasks_in_flight: 0, done_today: 0,
           failed_today: 0, spend_today_usd: 0, revenue_today_usd: null },
    inbox: { approvals: [], alerts: [] },
    ticker: [],
    ...over,
  };
}

export function ev(id: number, type: string, over: Partial<CityEvent> = {}): CityEvent {
  return { id, ts: "2026-11-27T00:00:00Z", type, from: null, to: null, run_id: null,
    task_id: null, agent_id: null, payload: {}, ...over };
}
