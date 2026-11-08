# Build Status

_Last updated: 2026-11-08, Phase 1 build._

This file reports only what was run and observed. "Configured" never means "verified".

## Test results

Run in the build sandbox (Linux, Python 3.12.x, Node 22, PostgreSQL 16 + pgvector 0.8.7 in Docker, Redis 7, Chromium 1243).

| Suite | Command | Result |
|---|---|---|
| Backend unit | `pytest tests/unit` | **63 passed** |
| Backend integration (real Postgres+pgvector, Redis, Chromium) | `pytest tests/integration` | **15 passed** |
| Live Anthropic API | `pytest tests/live -m live` | **2 skipped** (no `ANTHROPIC_API_KEY`) |
| Lint | `ruff check .` + `ruff format --check .` | pass |
| Types | `mypy jaxon` (51 files) | pass, 0 issues |
| Schema drift | `alembic check` | pass, models match migration `0001` |
| Migrations | `alembic upgrade head` / `downgrade base` round trip | pass (tested) |
| Frontend unit | `npm test` | **3 passed** |
| Frontend lint / types / build | `npm run lint`, `typecheck`, `build` | pass |
| Backend Docker image | `docker build ./backend` | builds and runs; `/health/ready` = ready |
| Frontend Docker image | `docker build ./frontend` | builds and runs; serves `/` (200) and proxies to the API |
| End-to-end browser check | Playwright: dashboard → submit run → worker → run page | pass |

**Total: 81 passed, 0 failed, 2 skipped.**

## What works (verified)

- **FastAPI server**: `/health`, `/health/ready` (checks DB, pgvector, migration revision, and Redis; returns 503 if any is down), OpenAPI docs.
- **API**: skills (list, get, invoke), agents (list, get, create, delete), runs (create queue/sync, list, get with step trace), memory (add, search, delete), system info.
- **Errors and logging**: one JSON error format, `x-request-id` on every response, structlog JSON logs carrying request and run IDs. Skill crashes and timeouts are contained.
- **Optional API token** (`JAXON_API_TOKEN`) enforced on `/api/v1/*` with a constant-time compare. Health endpoints stay open.
- **Database**: tables `agent_definitions`, `agent_runs`, `run_steps`, `memories` (vector(384) with an HNSW cosine index).
- **Memory**: pgvector similarity search with namespaces, using the deterministic `HashingEmbedder` (lexical, no external calls).
- **Skill system**: typed skills (class or `@skill` decorator), auto-discovery from packages and entry points, name validation, input validation, per-skill timeouts, an approval gate (`requires_approval`), and permissions declared in the manifests.
- **Built-in skills (8)**: `core.echo`, `core.current_time`, `math.calculate` (AST whitelist, no `eval`), `text.extract_contacts`, `web.fetch_url` (domain allowlist, no redirects, size cap), `browser.page_text` (real headless Chromium, allowlist enforced on every request including sub-resources), `memory.remember`, `memory.recall`.
- **Agent loop**: provider-independent. Handles tool calls (all results returned in one message), refusal, max_tokens, pause_turn, max_steps, provider errors, unknown or disallowed skills, and bad tool arguments. Each step is persisted.
- **Queue and worker**: Redis BLMOVE queue with a per-worker processing list. Crash recovery and idempotent execution (a finished run is never re-executed) are tested.
- **Dashboard**: health, system info, integrations, new-run form, recent runs, agents, skills, and a run detail page with step trace. Polling refresh. The API token stays server-side in the Next.js proxy route.
- **Docker Compose**: `docker compose config` validates.

## Works in tests but not proven against the real service

- **Anthropic provider.** Request building (model, system, tools, `output_config.effort`, refusal-fallback beta) and response parsing (text, tool_use, refusal, usage, error mapping) are tested through the real `anthropic` SDK against a **mocked HTTP transport**. No live API call has been made. Set `ANTHROPIC_API_KEY` and run `pytest tests/live -m live`.
- **Server-side refusal fallback** (`fallbacks: "default"` + beta `server-side-fallback-2026-07-01`) is on by default for the models that support it. It hasn't been exercised live. Set `ANTHROPIC_REFUSAL_FALLBACK=false` to turn it off.

## Environment limitations in this build

- **Docker Hub rate limits (429)**: official images were pulled through `mirror.gcr.io` instead. `docker compose up` was not run as a single command. The same flow was run with `docker run` on the built images: migrate, API, worker, web.
- **Sandbox TLS proxy**: containers here don't trust the sandbox's proxy CA, so the image builds were tested from a scratch copy with that CA injected. The committed Dockerfiles have no sandbox-specific changes and should build normally on a regular machine and in CI.
- **GitHub Actions** workflow was written but hasn't run yet. It runs on push to this branch.

## Missing credentials

| Credential | Needed for | Status |
|---|---|---|
| `ANTHROPIC_API_KEY` | Any agent run, plus live tests | **Not provided.** Runs fail cleanly with `configuration_error` |
| `JAXON_API_TOKEN` | API protection | Optional; unset = open API (development only) |
| `ALLOWED_WEB_DOMAINS` | `web.fetch_url`, `browser.page_text` | Not set; all outbound skill traffic is denied |
| `GITHUB_TOKEN` | Phase 4 self-improvement | Not used yet |

## Known gaps (planned, see ROADMAP.md)

- No streaming (the dashboard polls), no run cancellation, no automatic retry of failed runs.
- `requires_approval` skills are blocked for agents, but there is no approval UI or queue yet.
- Embeddings are lexical (hashing), not neural.
- Single shared API token; no user accounts.
- Mock components (only in tests): `tests/fixtures.py::ScriptedProvider`, and `httpx`/`httpx2` MockTransport in `test_builtin_skills.py` and `test_anthropic_provider.py`.
