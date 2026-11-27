// Shapes of GET /tenants/{t}/city/state and the /city/stream events (ADR-037).

export type AgentStatus =
  | "idle" | "working" | "done" | "waiting" | "failed" | "offline" | "paused" | "killed";

export interface Endpoint { kind: string; id: string }

export interface CityEvent {
  id: number;
  ts: string;
  type: string;
  from: Endpoint | null;
  to: Endpoint | null;
  run_id: string | null;
  task_id: string | null;
  agent_id: string | null;
  payload: Record<string, unknown>;
}

export interface Floor { id: string; key: string; name: string; level: number; agents: string[] }

export interface Department {
  id: string; key: string; name: string; color: string; slot: number; staffed: boolean;
  floors: Floor[];
}

export interface CurrentTask {
  id: string; title: string; status: string; started_at: string | null; run_id: string;
  command: string | null;
}

export interface Agent {
  id: string; name: string; role: string; key: string;
  department_id: string | null; floor_id: string | null;
  status: AgentStatus; online: boolean; model: string | null;
  last_heartbeat_at: string | null; status_changed_at: string | null;
  current_task: CurrentTask | null;
  cost_today_usd: number; done_today: number; failed_today: number;
  success_rate_7d: number | null;
}

export interface InboxApproval {
  id: string; question: string; category: string; amount_cents: number; reason: string;
  task_id: string; run_id: string; agent_id: string | null; created_at: string | null;
}

export interface Snapshot {
  generated_at: string;
  demo: boolean;
  last_event_id: number;
  jaxon: { events_per_minute: number; agents: string[] };
  departments: Department[];
  agents: Agent[];
  hud: {
    agents_online: number; agents_total: number; tasks_in_flight: number;
    done_today: number; failed_today: number; spend_today_usd: number;
    revenue_today_usd: number | null;
  };
  inbox: { approvals: InboxApproval[]; alerts: { level: string; kind: string; message?: string }[] };
  ticker: CityEvent[];
}

export interface AgentDetail extends Agent {
  trace: { command?: string; run_id?: string; type?: string; issued_at?: string | null;
           id?: string; title?: string; kind?: string; status?: string }[];
  time_on_task_s: number | null;
  log: CityEvent[]; tools: CityEvent[]; recent: CityEvent[];
  controls: { pause: boolean; resume: boolean; kill: boolean; reassign: boolean };
}

export interface JaxonView {
  commands: {
    run_id: string; command: string; type: string; status: string; at: string | null;
    routed_to: { task: string; status: string; agent: string; agent_id: string; department: string }[];
    routing_reasons: CityEvent[];
    memory: { offered: number | null; cited: unknown[] | null }[];
  }[];
}
