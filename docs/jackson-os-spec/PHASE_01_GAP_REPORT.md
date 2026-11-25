# Jackson OS — Phase 01 Gap Report

Date: 2026-11-25 · Repo: `STEADYLITT/Jaxon-core` · Inspected at commit `cb722b6` (before any changes in this PR)
Scope: phase 01 (inventory + gap report) delivered in full; phase 02 (scaffold + CI) delivered as a minimal, safe skeleton.
Update: phases 03–04 delivered — see [PHASE_03_04_REPORT.md](PHASE_03_04_REPORT.md); phase 05 — see [PHASE_05_REPORT.md](PHASE_05_REPORT.md); phase 06 — see [PHASE_06_REPORT.md](PHASE_06_REPORT.md); phase 07 — see [PHASE_07_REPORT.md](PHASE_07_REPORT.md); phase 08 — see [PHASE_08_REPORT.md](PHASE_08_REPORT.md); phase 09 — see [PHASE_09_REPORT.md](PHASE_09_REPORT.md); phase 10 — see [PHASE_10_REPORT.md](PHASE_10_REPORT.md). Status rows below are kept current.

---

## 0. Plain-English summary (for Steady)

- **`main` was basically empty**, so nothing was overwritten. **Correction:** an unmerged pull request (#1, Nov 8) holds a large earlier build: a Claude connection, skills, agents, memory and a dashboard. It wasn't caught in the first pass. It will be reused piece by piece (see the correction in §1).
- **Jackson v1 still lives in Base44 (Steady Growth OS).** This repo is where the real, long-term version gets built. Nothing in Growth OS was touched or read in this phase.
- **What I added:** the spec, this report, and an empty-but-correct skeleton: folders laid out per the spec, a tiny API that only answers "I'm alive", shared definitions (task states, commands, autonomy levels, entity names) in both languages, automatic tests, and a CI pipeline that runs those tests on every pull request.
- **Nothing is connected to the outside world.** No Shopify, Whop, OpenAI/Claude, Apify, ElevenLabs, FAL or Upload-Post calls exist. No keys were used or needed. No deployment exists.
- **Next step is phase 03: login and tenant isolation** — making sure only you can command Jackson and that each business's data stays walled off.

---

## 1. What already exists in the repo

| Item | Finding |
|---|---|
| Commits | 1 (`cb722b6`, "Initial commit: Jaxon core structure", 2025-09-16, author "Jaxon Vale") |
| Branches | `main`, plus this working branch |
| `README.md` | 1 line: "core brain & memory system for Jaxon Vale — AI partner" |
| `requirements.txt` | `flask`, `requests` — unpinned; **no code imports them** |
| Source code | none on `main` — **but see the PR #1 correction below** |
| Tests / CI | none |
| Secrets in history | none found (2 files, both inspected) |
| Repo name | Spec says `jaxon-os`; actual repo is `Jaxon-core`. Treated as the same project (ADR-001). |

**Outside this repo (reported by owner, not inspected):** Jackson v1 in Base44 "Steady Growth OS" — CEO chat, daily briefing, Decision Queue, Opportunity Board, Agent Factory (AgentSpecs), Second Brain (MemoryRecords), run log, and 6 agents: Lead-Gen (Apify), Marketing (Upload-Post), Voice (ElevenLabs), Ad Creation (FAL), Service & Follow-up, Social Trends.

### Correction (found after first draft): unmerged PR #1 holds substantial earlier work

The first inventory only inspected `main`. Branch `claude/modest-babbage-6qy5nq` (PR #1, 2026-11-08, unmerged, ~12k lines, 111 files) contains a working "Phase 1: JAXON OS core platform", built to an earlier plan. Its own BUILD_STATUS reports 81 tests passing in its sandbox. **On GitHub Actions its backend test job fails:** integration tests hit `relation "run_steps" does not exist` during table cleanup. The likely cause is the migration round-trip test leaving the DB without tables; this has not been confirmed.

| PR #1 component | Spec phase it covers | Gaps vs. spec | Plan |
|---|---|---|---|
| FastAPI app, JSON errors, request IDs, structlog | 02/05 | different layout (`backend/` vs `services/api/`) | port error/logging middleware in phase 05 |
| Single shared API token | 03 | no users, tenants or isolation | **superseded** by ADR-012/013 |
| Tables: agent_definitions, agent_runs, run_steps, memories | 04/05/09/12 | no `tenant_id`, no RLS | re-create under tenant scope when ported |
| Runs API + Redis queue + crash-recovering worker | 05/06 | spec says Temporal (ADR-006); no cancel, approvals or budget | ideas reused in 06 (ADR-016: Postgres queue + leases) |
| Anthropic provider (official SDK, mocked transport; never called live) | 08 | single provider; no OpenAI/local | port in 08 behind the adapter interface |
| Memory with pgvector + hashing embedder | 09 | no provenance or fact/inference/hypothesis kind | port in 09 and add provenance |
| Skill registry, `@skill`, timeouts, approval flag, allowlisted web fetch, Playwright skill | 10/11 | no per-tenant ToolGrant, cost or idempotency metadata | port in 10 |
| Agent loop + 3 built-in specialists, DB-backed agents | 12 | no quotas, depth caps or versioned AgentSpec | port in 12 |
| Next.js 15 dashboard (runs, agents, skills, health) | 19 (early) | no login, approvals inbox or global stop | becomes `apps/console` once Clerk is wired |

**Decision (ADR-014):** keep this PR's spec-ordered foundation (auth, tenancy, RLS) as the base. Port PR #1's proven pieces into it phase by phase, each gaining `tenant_id`, RLS and tests. Do not retrofit tenancy onto PR #1 directly: every table and endpoint would change anyway, and its layout differs from vol. 06. PR #1 stays open as the source to port from; after merging this PR, the owner can close it as superseded.

## 2. Gap analysis vs. spec

Legend: ✅ done · 🟡 scaffolded (shape only, no behaviour) · ❌ missing

| Spec area (volume) | Required | State after this PR | Phase |
|---|---|---|---|
| Repo layout (06) | apps/console, services/api, services/workers, packages/{contracts,policy,connectors}, skills, agents, workflows, infra, tests, docs | ✅ all directories exist, placeholders say "not built" | 02 |
| README, ADRs, env example, CI (06) | yes | ✅ README, ADRs (§3), `.env.example`, GitHub Actions | 02 |
| Migration scripts (06) | yes | ✅ Alembic `0001_initial` (phase 04) | 04 |
| Data contracts — 16 entities, task states (02) | IDs, timestamps, tenant scope, status | 🟡 names, enums and `BaseEntity` shape; no DB tables | 04/05 |
| Command types (04) | PLAN … STOP | ✅ typed commands; EXECUTE refused until phase 07 (ADR-015) | 05 |
| Command API (02) | POST /commands, GET /runs/{id}, POST /approvals/{id}/decision, POST /runs/{id}/cancel, GET /events | ✅ tenant-scoped commands, runs, cancel, SSE events (phase 05); approvals in 07 | 05 |
| Auth + tenant isolation (02/05) | | ✅ hosted-provider JWT + API scoping + Postgres RLS (phase 03) | 03 |
| PostgreSQL + pgvector, Redis (02) | | 🟡 Postgres schema for 5 entities; pgvector extension and Redis unused yet | 04 |
| Durable workflows (02) | retries, idempotency, DLQ, leases | ✅ Postgres queue with leases, retries, dead letters, quotas (ADR-016) | 06 |
| Policy, approvals, budget ledger (04) | autonomy 0–3, approval-required actions, reservations | ✅ policy engine, approvals inbox, append-only ledger with locked reservations (ADR-017) | 07 |
| Model adapters (01) | Claude, OpenAI, local | ✅ adapters + router + metering; labeled MOCK until keys exist; no live call verified (ADR-018) | 08 |
| Memory with provenance (01) | fact/inference/hypothesis, export/delete | ✅ provenance, confirmation of conflicts, versions, export, delete, retention; full-text recall (ADR-019) | 09 |
| Tool registry + sandbox (02) | schema, perms, cost, timeout, idempotency, verification | ✅ typed registry, owner grants, one gateway, redaction, call log; process-level sandbox (ADR-020) | 10 |
| Visual browser session (02) | streamed screenshots, take over, redaction | ❌ | 11 |
| Agent factory (05) | AgentSpec lifecycle, quotas, depth caps | ❌ | 12 |
| Opportunity / economics / Shopify / content / website / CRM (03) | | ❌ | 13–18 |
| Owner dashboard + global stop (04) | | ❌ | 19 |
| Security, backup/restore, E2E pilot | | ❌ | 20 |
| Growth OS import | (owner requirement) | ❌ design only (ADR-008, §6) | after 09/12 |

### Acceptance tests (vol. 06) — status

| Acceptance test | Status |
|---|---|
| Cross-tenant access denied | ✅ phase 03 — `services/api/tests/test_tenancy.py` |
| Unapproved spend blocked | ✅ phase 07 (`test_governance.py`) |
| Repeated request does not duplicate external action | ✅ command layer, phase 05 (`test_runs.py`); connectors must forward keys later |
| Failed worker resumes safely | ✅ phase 06 (`test_failed_worker_resumes_safely`) |
| Prompt injection cannot elevate grants | ✅ phase 07 policy + phase 10 tools (`test_injected_page_cannot_elevate_tool_grants`) |
| Agent cannot spawn beyond quota | ❌ phase 12 |
| Cancellation prevents new tasks | ✅ phase 05 (`test_cancellation_prevents_new_tasks`) |
| Secrets absent from logs | ✅ repo scan (02), model calls (08), tool inputs/outputs redacted (10) |
| Browser session visible | ❌ phase 11 |
| Memory cites source | ✅ phase 09 (`test_planner_cites_memory_sources`) |
| Sandbox Shopify draft verified | ❌ phase 15 |
| Website preview passes tests | ❌ phase 17 |
| Content remains draft until approved | ❌ phase 16 |

## 3. Architecture decisions (ADRs)

**ADR-001 — `Jaxon-core` is the Jackson OS repo.** The spec names `jaxon-os`; the only repo in scope is `STEADYLITT/Jaxon-core`. Its stated purpose ("core brain & memory") matches. Build here; rename later is optional and cheap.

**ADR-002 — Polyglot monorepo per vol. 06.** pnpm workspaces for TypeScript (`apps/*`, `packages/*`); each Python service has its own `pyproject.toml`. One CI workflow covers both. Rationale: spec layout, and console/contracts in TS with orchestration in Python.

**ADR-003 — FastAPI replaces Flask.** Flask was listed but never used. Spec mandates FastAPI (async, typed, OpenAPI out of the box). Root `requirements.txt` now points at `services/api`.

**ADR-004 — Contracts mirrored in TS and Python, enforced by a parity test.** `packages/contracts/src/index.ts` and `services/api/jackson_api/contracts.py` must match; `tests/test_repo.py` fails CI if they drift. In phase 05, switch to a single source (OpenAPI generated from FastAPI → TS types) and keep the parity test as a guard.

**ADR-005 — PostgreSQL (pgvector) is canonical; Redis is cache only.** Vector search supplements records, never replaces them (vol. 00). Local dev via `infra/docker-compose.yml`, bound to 127.0.0.1. Managed hosting decided later (vol. 02).

**ADR-006 — (Superseded by ADR-016.) Temporal for durable workflows, decided at phase 06 gate.** Default per spec. If self-hosting Temporal proves too heavy for a single-owner pilot, fallback is Temporal Cloud; a Postgres-backed queue is the last resort and would require a new ADR because it gives up durable timers/replay.

**ADR-007 — Connector modes: `mock` → `sandbox` → `live`.** Every connector result carries its mode. Mocks are the default and must say "mock" in output and UI. Moving to `live` requires owner-provided credentials and an explicit approval. No connector may be called "integrated" while in mock mode (vol. 00 definition of done).

**ADR-008 — Growth OS is an import source, not a runtime dependency.** Jackson OS's schema is a superset of Growth OS. A one-way, idempotent importer will read a Base44 export, write records with provenance (`source_system = "base44_growth_os"`, `source_id`, `imported_at`), and never write back. Re-running an import must not duplicate rows. Mapping in §6.

**ADR-009 — Secrets never in Git, prompts or logs.** `.env.example` holds names only (test-enforced); real values go in a local `.env` (git-ignored) or a secret manager. CI scans tracked files for common key formats.

**ADR-010 — Tenancy: one owner tenant first, `tenant_id` on every row from day one.** Steady's businesses (Steady Trades University, CSW Trades, Steady Ventures, APEX-7, Shopify store) are `Business` rows under the owner tenant. Steady Ventures **client** work gets its own tenants (vol. 03: isolated client workspaces).

**ADR-012 — Hosted auth (Clerk recommended); the API verifies tokens provider-neutrally.** Decided with the owner 2026-11-25 (cost not a concern). Passwords, MFA, email verification and account recovery are handled by the provider, which is safer than building them. The API only verifies RS256 JWTs against the provider's JWKS (`iss`, `exp`/`nbf`, required `sub`, optional `aud`, optional `azp` allow-list), so Clerk can be swapped for Auth0/WorkOS by changing env vars. No auto-provisioning: a valid token for an unknown user gets 403; users are created by the owner (`jackson_api.seed`). **Unverified:** Clerk's current docs were unreachable from the build machine (network block), so Clerk-specific claim details (`azp` behaviour, JWKS URL) must be confirmed against clerk.com before the console goes live.

**ADR-013 — Tenant isolation is enforced twice: in the API and in Postgres.** (1) Every tenant route checks membership and filters by `tenant_id`. (2) Postgres row-level security on tenant tables only shows rows matching `app.tenant_id`, set per transaction with `set_config(..., is_local => true)`; with no tenant set, nothing is visible. The API connects as `jackson_app` (not owner, not superuser, no BYPASSRLS — test-enforced). `audit_events` is append-only for that role. Users and memberships are global (one person can belong to several tenants, e.g. agency clients); this deviates slightly from "tenant scope on every entity" and is deliberate.

**ADR-015 — Commands are idempotent and tenant-scoped, and EXECUTE stays locked until policy exists.** Commands live at `/tenants/{id}/commands`, not the spec's bare `/commands`, so the tenant is always explicit and ADR-013 applies. Every command needs an `idempotency_key`. Same key with the same body returns the original run (200, `Idempotent-Replay: true`). Same key with a different body returns 409. A unique constraint backs this up under concurrency. EXECUTE returns 422 until phase 07, because with no ToolGrants nothing is "already within granted scope" (vol. 04). PAUSE/RESUME/STOP are idempotent by state. The spec's task states don't include "paused", so pausing is a `paused` flag that workers must respect. Run history (`run_events`) is append-only, and its ids double as SSE event ids.

**ADR-016 — Durable execution uses a Postgres-backed queue, not Temporal (supersedes ADR-006).** Tasks are claimed with `SELECT … FOR UPDATE SKIP LOCKED` and leases, with heartbeat, exponential-backoff retries, dead-lettering and per-tenant quotas. This keeps one source of truth, transactional consistency between queue state and run state, and no extra infrastructure. The worker runs as role `jackson_worker`, which has cross-tenant access to queue tables only, no deletes, append-only history, and no users/memberships. Revisit Temporal if we need multi-week timers, complex sagas, or more than roughly 50 claims per second.

**ADR-017 — Policy decides from database facts only; approvals and money are DB-enforced.** `policy.evaluate()` is pure, and its inputs (business autonomy level, remaining run budget, an approval matching the exact action fingerprint) are loaded from Postgres, never from model or tool text. Restricted categories always need approval. `live_trade` is always denied (ADR-011). Spend runs autonomously only at level 3 and within budget. Approvals can be decided only through the API by owner/admin; the worker role has INSERT but no UPDATE on approvals, and no UPDATE on businesses or the ledger. Budget reservations lock the run row, so concurrent reservations serialize. EXECUTE is accepted when it names a business, and every action inside still passes policy (this updates ADR-015).

**ADR-018 — Model access goes through one metered gateway, Claude Opus 5.5 by default, and MOCK when nothing is configured.** Handlers reach models only via `ctx.call_model(profile, request)`, which checks the tenant's monthly model budget (`MODEL_MONTHLY_BUDGET_USD`, default 20; a soft cap that can overshoot by at most one call), routes by profile, calls, and writes an append-only `model_usage` row plus a `model.called` event. Default routes keep every profile on `claude-opus-5-5` and vary `output_config.effort` (planning high, standard medium, fast low). Cheaper models are an owner choice via env. Claude calls use server-side refusal fallback and check `stop_reason == "refusal"`. OpenAI and local OpenAI-compatible providers stay off until model name (and, for OpenAI, prices) are configured. No model name is guessed. Model spend is operating cost metered separately from the phase 07 business ledger, so it doesn't need per-call approval.

**ADR-019 — Memory is canonical rows with provenance; agents append, people decide.** `memory_records` stores kind, epistemic status, key, impact, confidence, source (type/ref/detail), scope, status, a version chain and retention. Owner corrections create new versions; deletion removes all versions, and the audit log keeps ids only. A conflicting high-impact write waits for confirmation. Agents can INSERT and SELECT but never UPDATE or DELETE, so any conflict they cause waits for an owner, and they can't confirm their own facts. Agents recall `tenant`-scope memories only. Recall uses Postgres full-text ranking. pgvector embeddings are deferred until an embedding provider is chosen, and will supplement, not replace, these rows.

**ADR-020 — One tool gateway; grants live only in the database.** Tools declare schema, policy category, cost, timeout, idempotency and verification method in a registry. Handlers use tools only via `ctx.use_tool()`, which applies these steps in order: grant check (owner-created `tool_grants`; business-specific grant first, then tenant-wide) → schema validation → budget reservation for paid tools → policy check of the category → deduplication by `run:sequence:tool:input-hash` → timeout → redaction → append-only `tool_calls` log, with output marked untrusted. The worker role can read grants but never write them. `code.run_python` is process-level isolation only and needs autonomy level 2. A container sandbox is a phase 20 prerequisite for untrusted code.

**ADR-011 — Trading is simulation-only.** APEX-7 and any trading work are backtest/paper-trade only (vol. 00, vol. 03). No live-trade connector will be built without a new ADR and explicit owner approval.

## 4. Dependency inventory

### Added in this PR (pinned or bounded)

| Dependency | Where | Version | Purpose |
|---|---|---|---|
| fastapi | services/api | >=0.115,<1 (tested 0.165.0) | API framework |
| pydantic | services/api | >=2.7,<3 (tested 2.14.4) | validation |
| uvicorn | services/api | >=0.30,<1 (tested 0.54.0) | ASGI server |
| pytest, httpx, ruff | services/api dev | tested 9.2.0 / 0.28.1 / 0.17.6 | tests, test client, lint |
| typescript | packages/contracts dev | 5.9.3 | typecheck |
| vitest | packages/contracts dev | 3.2.7 | TS tests |
| pnpm | root | 10.28.0 | workspace manager |
| sqlalchemy, alembic, psycopg[binary], pyjwt[crypto] | services/api (phase 03–04) | tested 2.1.9 / 1.20.1 / 3.3.9 / 2.15.1 | DB, migrations, JWT |
| anthropic, openai | services/api (phase 08) | tested 2.14.0 / 3.59.0 | official model SDKs (httpx2 transport) |
| Node | runtime | >=22.12 | |
| Python | runtime | >=3.12 (CI 3.12, local 3.13) | |

### Removed
`flask`, `requests` (unused; see ADR-003). `requests` will return only if a connector needs it; `httpx` is preferred.

### Infrastructure (local only, not deployed)
`pgvector/pgvector:pg16`, `redis:7-alpine` via docker-compose. Planned: MinIO/S3 (artifacts), Playwright (11). Temporal not needed (ADR-016).

## 5. Credential inventory — names only, none provided

| Variable name | For | Needed by phase | Status |
|---|---|---|---|
| `POSTGRES_PASSWORD` | local DB | 04 | owner sets locally |
| `AUTH_ISSUER`, `AUTH_JWKS_URL`, `AUTH_AUDIENCE`, `AUTH_AUTHORIZED_PARTIES` | API token verification (public values) | 03 | not provided |
| `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` | console sign-in (ADR-012) | console build | not provided |
| `DATABASE_URL`, `MIGRATION_DATABASE_URL` | app role / schema owner | 04 | owner sets locally |
| `WORKER_DATABASE_URL` | worker (role `jackson_worker`) | 06 | owner sets locally |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | artifacts | 09–11 | not provided |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | model adapters | 08 | not provided |
| `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_ADMIN_API_TOKEN` | Shopify **development** store, draft products only | 15 | not provided |
| `WHOP_API_KEY` | Steady Trades University checkout data (read-only first) | 14/19 | not provided |
| `APIFY_API_TOKEN` | lead-gen / research | 13/18 | not provided |
| `UPLOAD_POST_API_KEY` | social scheduling (drafts until approved) | 16 | not provided |
| `ELEVENLABS_API_KEY` | voice | 16 | not provided |
| `FAL_API_KEY` | ad/media generation | 16 | not provided |
| `BASE44_APP_ID`, `BASE44_API_KEY` | Growth OS import | post-09 | not provided |

All connectors run as labeled mocks until these exist and are approved (ADR-007). Exact scopes will be listed per connector after checking current provider docs, at the time each is built.

## 6. Growth OS → Jackson OS import mapping (draft)

Field-level mapping needs a real Base44 export; this is the entity-level plan.

| Growth OS (Base44) | Jackson OS entity | Notes |
|---|---|---|
| CEO chat | `WorkflowRun` (+ `MemoryRecord` for durable facts) | each chat command → typed `Command` |
| Daily briefing | `Artifact` from a `REPORT` command | |
| Decision Queue | `Approval` | pending items import as `waiting_approval` |
| Opportunity Board | `Opportunity` (linked to `Business`) | keep source links/evidence |
| Agent Factory / AgentSpecs | `AgentSpec` (version 1, status `imported`) | must pass policy validation before activation |
| Second Brain MemoryRecords | `MemoryRecord` | provenance = base44; default kind `hypothesis` unless marked as fact |
| Run log | `WorkflowRun` / `Task` / `AuditEvent` | historical, read-only |
| 6 agents | `AgentSpec` + `ToolGrant` per connector | Lead-Gen→Apify, Marketing→Upload-Post, Voice→ElevenLabs, Ad Creation→FAL, Service & Follow-up, Social Trends |
| Businesses | `Business` under owner `Tenant` | STU, CSW Trades, Steady Ventures, APEX-7, Shopify store |

## 7. Phase plan

### Phase 01 — inventory + gap report ✅ (this document)
Delivered: repo inventory (§1), gap analysis (§2), ADRs (§3), dependency inventory (§4), credential inventory (§5), Growth OS mapping (§6).

### Phase 02 — scaffold + CI ✅ (minimal)
Delivered:
- Spec directory layout with "not built" placeholders.
- `services/api`: FastAPI app with `GET /health` and `GET /contracts` only.
- `packages/contracts`: TS enums/types for task states, commands, autonomy levels, 16 entities, `BaseEntity`, `Command`.
- `tests/test_repo.py`: layout, spec files present, `.env.example` names-only, TS↔Python parity, no secrets in tracked files.
- `.github/workflows/ci.yml`: Python lint+tests, TS typecheck+tests, compose validation.
- `infra/docker-compose.yml`, `.env.example`, `.gitignore`, `ruff.toml`.

Not done in phase 02 (intentionally): Next.js console app, workers runtime, Temporal, migrations.

### Phase 03 — auth + tenant isolation (next)
Plan: owner login (decision needed: hosted auth such as Clerk/Auth0 vs. self-managed), `Tenant`/`User` models, tenant-scoped request context, deny-by-default middleware, acceptance test "cross-tenant access denied". Depends on phase 04's DB for persistence, so 03 and 04 may be done together as one PR.

## 8. Blockers and owner decisions

1. ~~**Auth provider choice**~~ — decided 2026-11-25: hosted, Clerk recommended (ADR-012).
2. **Hosting choice** (spec: after load/security tests) — not needed until phase 20.
3. **Growth OS export**: a Base44 data export (or API access) is needed to finalize §6 field mapping.
4. **Credentials** (§5): none needed until phase 08; nothing blocks phases 03–07.
5. **Repo name**: `Jaxon-core` vs spec `jaxon-os` — cosmetic, no action needed.
