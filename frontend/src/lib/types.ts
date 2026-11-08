export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface SkillManifest {
  name: string;
  description: string;
  version: string;
  tags: string[];
  permissions: string[];
  requires_approval: boolean;
  input_schema: Record<string, unknown>;
}

export interface AgentSpec {
  name: string;
  description: string;
  system_prompt: string;
  skills: string[];
  model: string | null;
  effort: string | null;
  max_steps: number;
  builtin: boolean;
}

export interface RunStep {
  index: number;
  kind: "model_response" | "skill_call" | "skill_result";
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Run {
  id: string;
  agent_name: string;
  status: RunStatus;
  input: string;
  output: string | null;
  error: ApiError | null;
  model: string | null;
  stop_reason: string | null;
  usage: { input_tokens?: number; output_tokens?: number };
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  steps?: RunStep[];
}

export interface Integration {
  name: string;
  configured: boolean;
  verified: boolean;
  required_env: string[];
  notes: string;
}

export interface SystemInfo {
  name: string;
  version: string;
  environment: string;
  model_provider: string;
  default_model: string;
  skills: number;
  queue_depth: number | null;
  integrations: Integration[];
}

export interface Readiness {
  status: "ready" | "not_ready";
  checks: Record<string, { ok: boolean; [k: string]: unknown }>;
}
