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

/** Typed command shape (vol. 04). Validation/enforcement arrives in phase 05. */
export interface Command {
  type: CommandType;
  objective: string;
  businessId?: string;
  budgetCents?: number;
  deadline?: string;
  constraints?: string[];
  successCriteria?: string[];
  idempotencyKey: string;
}

export function isTerminal(state: TaskState): boolean {
  return TERMINAL_TASK_STATES.has(state);
}
