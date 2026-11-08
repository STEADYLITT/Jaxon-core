import type {
  AgentSpec,
  ApiError,
  Readiness,
  Run,
  SkillManifest,
  SystemInfo,
} from "./types";

// All browser calls go through the Next.js proxy route (/api/jaxon/*), which adds the
// optional server-side API token. The token is never shipped to the browser.
const BASE = "/api/jaxon";

export class JaxonApiError extends Error {
  constructor(
    public status: number,
    public body: ApiError,
  ) {
    super(body.message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok && !(path === "/health/ready" && res.status === 503)) {
    const err: ApiError = data?.error ?? { code: "http_error", message: res.statusText };
    throw new JaxonApiError(res.status, err);
  }
  return data as T;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/health"),
  ready: () => request<Readiness>("/health/ready"),
  system: () => request<SystemInfo>("/api/v1/system"),
  skills: () => request<SkillManifest[]>("/api/v1/skills"),
  agents: () => request<AgentSpec[]>("/api/v1/agents"),
  runs: (limit = 20) => request<{ items: Run[]; total: number }>(`/api/v1/runs?limit=${limit}`),
  run: (id: string) => request<Run>(`/api/v1/runs/${encodeURIComponent(id)}`),
  createRun: (agent: string, input: string, mode: "queue" | "sync" = "queue") =>
    request<Run>("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify({ agent, input, mode }),
    }),
};
