# JAXON OS Roadmap

Status key: ✅ done and tested · 🟡 partly done · ⬜ not started

## Phase 1: Core platform ✅

- ✅ Monorepo: `backend/` (api, agents, skills, providers, memory, queue, workers, integrations), `frontend/`, Docker, CI
- ✅ FastAPI server with liveness and readiness endpoints
- ✅ Next.js dashboard: health, integrations, skills, agents, runs, and run traces
- ✅ PostgreSQL + pgvector schema and Alembic migration `0001`
- ✅ Skill interface, decorator, registry, auto-discovery, approval gate, and 8 built-in skills
- ✅ Agent execution loop behind a provider-independent model interface
- ✅ Anthropic provider (request building and SDK path tested with mocked HTTP; **live API not yet verified**)
- ✅ Redis run queue and worker with crash recovery
- ✅ Structured JSON logging, request IDs, and a uniform error format
- ✅ Docker Compose, GitHub Actions CI, and docs

## Phase 2: Make agents useful day to day ⬜

1. **Verify the live Anthropic path**: set `ANTHROPIC_API_KEY` and run `pytest tests/live -m live`. Add the key as a CI secret for a manual-trigger live job.
2. **Streaming run updates**: SSE endpoint plus a live trace in the dashboard (polling for now).
3. **Human approval queue**: persist pending `requires_approval` skill calls, add approve/reject in the dashboard, and resume the run afterwards.
4. **Neural embeddings**: add an `EmbeddingProvider` (e.g. Voyage) with a migration for the new vector size. Keep `HashingEmbedder` as the offline fallback.
5. **Run cancellation and retries**: `cancelled` status, retry policy for retryable provider errors, and a dead-letter list.
6. **Auth**: real user accounts on the dashboard, replacing the single shared token.
7. **Prompt caching** on system prompts and tools to cut cost on long agent loops.

## Phase 3: Business workflows ⬜

- Workflow engine: multi-step, multi-agent pipelines defined as data (DAG of agent and skill steps), with scheduling.
- Opportunity discovery: research agent plus a scoring rubric, stored as `opportunities`.
- Lead generation: `leads` table, enrichment skills on authorized sources only, de-duplication, and CRM export. Respect site terms and anti-spam law.
- Integrations: email (draft-only until approved), calendar, CRM, GitHub.
- Cost tracking per run and agent, plus budgets.

## Phase 4: Self-improvement through reviewed PRs ⬜

- `dev` agent with repo-read, test-run, and branch/PR skills, all `requires_approval=True`.
- Runs only in a sandboxed checkout, opens PRs, and never merges. A human reviews every change, and CI must pass.
- Evaluation suite to measure whether a change improved agent outcomes.

## Phase 5: Scale ⬜

- Horizontal workers, per-queue priorities, rate limiting per provider.
- Skill marketplace layout: skills shipped as separate packages through the `jaxon.skills` entry point.
- Observability: OpenTelemetry traces across API, worker, and model calls.
