# JAXON OS

JAXON OS is a modular AI operating system for running a business. It coordinates specialist AI agents, runs them through typed skills, stores long-term memory in PostgreSQL + pgvector, and queues work through Redis.

This is **Phase 1**: the core platform. See [BUILD_STATUS.md](BUILD_STATUS.md) for what works and how it was tested, and [ROADMAP.md](ROADMAP.md) for what comes next.

```
┌──────────────┐  /api/jaxon/*   ┌──────────────────────────────┐
│  Next.js     │ ──(proxy, adds──▶  FastAPI  (jaxon.api)        │
│  dashboard   │   API token)    │   /health  /api/v1/*          │
└──────────────┘                 └──────┬──────────────┬─────────┘
                                        │ enqueue      │ sync runs
                                  ┌─────▼─────┐  ┌─────▼──────────────────────┐
                                  │  Redis    │──▶ Worker (jaxon.workers)     │
                                  │  queue    │  │  AgentExecutor loop:       │
                                  └───────────┘  │  ModelProvider ⇄ Skills    │
                                                 └─────┬──────────────┬───────┘
                                                       │              │
                                          ┌────────────▼───┐   ┌──────▼─────────┐
                                          │ PostgreSQL +   │   │ Anthropic API  │
                                          │ pgvector       │   │ (any provider) │
                                          └────────────────┘   └────────────────┘
```

## Repository layout

```
backend/                 Python 3.12 package `jaxon`
  jaxon/core/            settings (env), structured logging, error hierarchy
  jaxon/api/             FastAPI app factory, routes, request schemas
  jaxon/providers/       provider-independent model interface + Anthropic implementation
  jaxon/agents/          AgentSpec, built-in specialists, the agent execution loop
  jaxon/skills/          Skill interface, registry/discovery, built-in skills
  jaxon/memory/          embedding interface + pgvector memory store
  jaxon/queue/           Redis run queue
  jaxon/workers/         background worker that executes queued runs
  jaxon/integrations/    integration status (credentials present ≠ verified)
  jaxon/services/        run and agent lifecycle (DB persistence)
  migrations/            Alembic migrations
  tests/unit             no external services
  tests/integration      real PostgreSQL+pgvector, Redis, Chromium
  tests/live             real Anthropic API (skipped without a key)
frontend/                Next.js 15 + TypeScript + Tailwind dashboard
docker-compose.yml       local stack: postgres, redis, migrate, api, worker, web
.github/workflows/ci.yml lint, type-check, tests, builds
```

## Quick start (Docker)

Requires Docker with Compose v2.

```bash
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY (needed for agent runs)
docker compose up --build
```

- Dashboard: http://localhost:3000
- API: http://localhost:8000 (OpenAPI docs at http://localhost:8000/docs)
- Readiness: http://localhost:8000/health/ready

The `migrate` service runs `alembic upgrade head` before the API and worker start.

## Local development (without Docker for the app)

Requires Python 3.12, Node 22, and a running PostgreSQL 16 with pgvector and Redis 7. The easiest way to get those is `docker compose up -d postgres redis` (or `make infra`).

```bash
make setup      # venv + backend deps (editable) + frontend deps
make migrate    # alembic upgrade head
make api        # FastAPI on :8000 with reload
make worker     # background worker
make web        # Next.js dev server on :3000
```

Without `make`:

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e "backend[dev]"
cd backend && alembic upgrade head && uvicorn jaxon.main:app --reload
# separate shells:
cd backend && python -m jaxon.workers.run_worker
cd frontend && npm ci && npm run dev
```

## Configuration

All configuration comes from environment variables (or a `.env` file). See [.env.example](.env.example) for the full list. Nothing in the repository contains a credential.

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+asyncpg://…` |
| `REDIS_URL` | yes | Redis for the run queue |
| `ANTHROPIC_API_KEY` | for agent runs | Claude API key. Without it, runs fail with `configuration_error`; everything else still works |
| `DEFAULT_MODEL` | no | Default `claude-opus-5-5` |
| `MODEL_EFFORT` | no | `low` … `max`, default `high` |
| `ANTHROPIC_REFUSAL_FALLBACK` | no | Default `true`. Sends Anthropic's server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) on models that support it. Set `false` to turn it off |
| `JAXON_API_TOKEN` | no | If set, `/api/v1/*` requires `Authorization: Bearer <token>`. The dashboard proxy adds it server-side |
| `ALLOWED_WEB_DOMAINS` | for web skills | Comma-separated allowlist for `web.fetch_url` and `browser.page_text`. Empty means all outbound skill traffic is denied |
| `SKILL_PACKAGES` | no | Extra Python packages to scan for skills |
| `GITHUB_TOKEN` | no | Reserved for the Phase 3 self-improvement loop |

## API

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/health/ready` | DB, pgvector, migration revision, Redis. Returns 503 if any of them is down |
| GET | `/api/v1/system` | Version, provider, skill count, queue depth, integration status |
| GET | `/api/v1/skills` | Skill manifests (JSON schemas, permissions). `?tag=` filter |
| POST | `/api/v1/skills/{name}/invoke` | Run a skill directly: `{"arguments": {...}, "approved": false}` |
| GET/POST | `/api/v1/agents` | List the built-in and custom agents, or create a custom agent |
| GET/DELETE | `/api/v1/agents/{name}` | Get or delete a custom agent |
| POST | `/api/v1/runs` | `{"agent","input","mode":"queue"\|"sync"}` |
| GET | `/api/v1/runs`, `/api/v1/runs/{id}` | Run history, and one run with its step-by-step trace |
| POST | `/api/v1/memory`, `/api/v1/memory/search` | Store or vector-search memory |
| DELETE | `/api/v1/memory/{id}` | Delete a memory item |

Errors always look like `{"error": {"code": "...", "message": "...", "details": {...}}}`, and every response carries an `x-request-id` header that also appears in the logs.

## Adding a skill

Create a module in any package the registry scans (`jaxon/skills/builtin/`, a package listed in `SKILL_PACKAGES`, or a separately installed package that exposes a `jaxon.skills` entry point). The core needs no edits.

```python
from pydantic import Field
from jaxon.skills.base import Permission, SkillContext, SkillInput, SkillOutput, skill

class QuoteIn(SkillInput):
    hours: float = Field(gt=0)
    rate: float = Field(gt=0)

class QuoteOut(SkillOutput):
    total: float

@skill(name="sales.quote", description="Price a job from hours and hourly rate.",
       tags=("sales",), permissions=())
async def quote(inp: QuoteIn, ctx: SkillContext) -> QuoteOut:
    return QuoteOut(total=round(inp.hours * inp.rate, 2))
```

The registry validates the name, input, output, and timeout. Invalid input, timeouts, and crashes come back as structured errors and never take down the agent loop. Mark anything that changes the outside world (sending email, payments, commits) with `requires_approval=True`. Agents can't run those skills until a human approves.

## Adding a model provider

Implement `jaxon.providers.base.ModelProvider.complete()` and register it:

```python
from jaxon.providers.registry import register_provider
register_provider("myprovider", lambda settings: MyProvider(settings))
```

Then set `MODEL_PROVIDER=myprovider`. Agents only see the neutral `ChatMessage`, `ToolSpec`, and `CompletionResponse` types. Provider-native content (for example Claude thinking blocks) is replayed verbatim to the provider that produced it.

## Testing

```bash
cd backend
pytest -m "not live"            # unit + integration (integration needs Postgres+pgvector and Redis)
pytest tests/unit               # no external services
ANTHROPIC_API_KEY=... pytest tests/live -m live   # real API calls, costs money
cd ../frontend && npm test && npm run typecheck && npm run lint && npm run build
```

Integration tests skip themselves if Postgres or Redis can't be reached. Mock components live only in `backend/tests/fixtures.py` (`ScriptedProvider`) and in clearly labeled `httpx` MockTransport tests.

## Deployment

Not deployed. The Docker images are production-shaped: non-root user, standalone Next.js build, and migrations run as a separate step. Before a real deployment:

1. Use managed PostgreSQL with pgvector and managed Redis. Set `DATABASE_URL` and `REDIS_URL`.
2. Set `ENVIRONMENT=production`, a strong `JAXON_API_TOKEN`, and `CORS_ORIGINS` for the dashboard origin.
3. Run `alembic upgrade head` (the `migrate` image command) before rolling out the API and worker.
4. Run the API (`uvicorn jaxon.main:app`), one or more workers (`python -m jaxon.workers.run_worker`, each with a unique `WORKER_ID`), and the web image with `JAXON_API_URL` pointing to the API.
5. Put the dashboard behind real user authentication. Phase 1 has only the shared API token.
