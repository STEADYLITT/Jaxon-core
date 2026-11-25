/**
 * Jackson OS shared contracts (spec volume 02 "Data contracts", volume 04 "Command interface").
 *
 * MUST stay identical to services/api/jackson_api/contracts.py.
 * tests/test_contract_parity.py enforces this in CI.
 */

export const TASK_STATES = [
  "queued",
  "running",
  "waiting_approval",
  "succeeded",
  "failed",
  "canceled",
] as const;
export type TaskState = (typeof TASK_STATES)[number];

export const TERMINAL_TASK_STATES: ReadonlySet<TaskState> = new Set<TaskState>([
  "succeeded",
  "failed",
  "canceled",
]);

export const COMMAND_TYPES = [
  "PLAN",
  "EXECUTE",
  "SIMULATE",
  "REPORT",
  "OPTIMIZE",
  "BUILD",
  "PAUSE",
  "RESUME",
  "STOP",
] as const;
export type CommandType = (typeof COMMAND_TYPES)[number];

export const AUTONOMY_LEVELS = {
  READ_ONLY: 0,
  DRAFTS_AND_SIMULATIONS: 1,
  REVERSIBLE_SANDBOX: 2,
  APPROVED_BOUNDED_PRODUCTION: 3,
} as const;
export type AutonomyLevel = (typeof AUTONOMY_LEVELS)[keyof typeof AUTONOMY_LEVELS];

export const ENTITY_NAMES = [
  "Tenant",
  "User",
  "Business",
  "Opportunity",
  "AgentSpec",
  "AgentInstance",
  "SkillVersion",
  "ToolGrant",
  "WorkflowRun",
  "Task",
  "Artifact",
  "Approval",
  "Budget",
  "LedgerEntry",
  "AuditEvent",
  "MemoryRecord",
] as const;
export type EntityName = (typeof ENTITY_NAMES)[number];

/** Fields every entity must carry (spec: "IDs, timestamps, tenant scope and status required"). */
export interface BaseEntity {
  id: string;
  tenantId: string;
  status: string;
  createdAt: string; // ISO-8601 UTC
  updatedAt: string; // ISO-8601 UTC
}

/**
 * Typed command (vol. 04) exactly as POST /tenants/{tenantId}/commands accepts it.
 * Field names match the API wire format (snake_case).
 * - PLAN/SIMULATE/REPORT/OPTIMIZE/BUILD create a run (objective required).
 * - PAUSE/RESUME/STOP act on `target_run_id`.
 * - EXECUTE is refused (422) until the policy engine exists (phase 07).
 */
export interface Command {
  type: CommandType;
  idempotency_key: string; // 8-200 chars; same key + same body => same run
  objective?: string;
  business_id?: string;
  budget_cents?: number;
  deadline?: string; // ISO-8601
  constraints?: string[];
  success_criteria?: string[];
  target_run_id?: string;
}

export interface TaskOut {
  id: string;
  sequence: number;
  kind: string;
  title: string;
  status: TaskState;
  created_at: string;
}

export interface WorkflowRun {
  id: string;
  tenant_id: string;
  business_id: string | null;
  command_type: CommandType;
  objective: string;
  budget_cents: number | null;
  deadline: string | null;
  constraints: string[];
  success_criteria: string[];
  idempotency_key: string;
  status: TaskState;
  paused: boolean;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  tasks: TaskOut[];
}

/** Payload of each SSE message from GET /tenants/{tenantId}/events. */
export interface RunEventMessage {
  run_id: string;
  type: string; // run.created | task.created | run.state_changed | run.paused | run.resumed
  data: Record<string, unknown>;
  created_at: string;
}

export function isTerminal(state: TaskState): boolean {
  return TERMINAL_TASK_STATES.has(state);
}
