# Jackson OS — Phase 01 Gap Report

Date: 2026-11-25 · Repo: `STEADYLITT/Jaxon-core` · Inspected at commit `cb722b6` (before any changes in this PR)
Scope: phase 01 (inventory + gap report) delivered in full; phase 02 (scaffold + CI) delivered as a minimal, safe skeleton.

---

## 0. Plain-English summary (for Steady)

- **The repo was basically empty.** It had a one-line README and a list of two Python libraries. No working code existed, so nothing was overwritten.
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
| Source code | none |
| Tests / CI | none |
| Secrets in history | none found (2 files, both inspected) |
| Repo name | Spec says `jaxon-os`; actual repo is `Jaxon-core`. Treated as the same project (ADR-001). |

**Outside this repo (reported by owner, not inspected):** Jackson v1 in Base44 "Steady Growth OS" — CEO chat, daily briefing, Decision Queue, Opportunity Board, Agent Factory (AgentSpecs), Second Brain (MemoryRecords), run log, and 6 agents: Lead-Gen (Apify), Marketing (Upload-Post), Voice (ElevenLabs), Ad Creation (FAL), Service & Follow-up, Social Trends.

## 2. Gap analysis vs. spec

Legend: ✅ done · 🟡 scaffolded (shape only, no behaviour) · ❌ missing

| Spec area (volume) | Required | State after this PR | Phase |
|---|---|---|---|
| Repo layout (06) | apps/console, services/api, services/workers, packages/{contracts,policy,connectors}, skills, agents, workflows, infra, tests, docs | ✅ all directories exist, placeholders say "not built" | 02 |
| README, ADRs, env example, CI (06) | yes | ✅ README, ADRs (§3), `.env.example`, GitHub Actions | 02 |
| Migration scripts (06) | yes | ❌ | 04 |
| Data contracts — 16 entities, task states (02) | IDs, timestamps, tenant scope, status | 🟡 names, enums and `BaseEntity` shape; no DB tables | 04/05 |
| Command types (04) | PLAN … STOP | 🟡 enum + `Command` type; no endpoint | 05 |
| Command API (02) | POST /commands, GET /runs/{id}, POST /approvals/{id}/decision, POST /runs/{id}/cancel, GET /events | ❌ deliberately absent until auth exists (test enforces 404) | 05 |
| Auth + tenant isolation (02/05) | | ❌ | 03 |
| PostgreSQL + pgvector, Redis (02) | | 🟡 local docker-compose only | 04 |
| Durable workflows — Temporal (02) | retries, idempotency, DLQ, leases | ❌ | 06 |
| Policy, approvals, budget ledger (04) | autonomy 0–3, approval-required actions, reservations | 🟡 autonomy levels defined; no enforcement | 07 |
| Model adapters (01) | Claude, OpenAI, local | ❌ none connected | 08 |
| Memory with provenance (01) | fact/inference/hypothesis, export/delete | ❌ | 09 |
| Tool registry + sandbox (02) | schema, perms, cost, timeout, idempotency, verification | ❌ | 10 |
| Visual browser session (02) | streamed screenshots, take over, redaction | ❌ | 11 |
| Agent factory (05) | AgentSpec lifecycle, quotas, depth caps | ❌ | 12 |
| Opportunity / economics / Shopify / content / website / CRM (03) | | ❌ | 13–18 |
| Owner dashboard + global stop (04) | | ❌ | 19 |
| Security, backup/restore, E2E pilot | | ❌ | 20 |
| Growth OS import | (owner requirement) | ❌ design only (ADR-008, §6) | after 09/12 |

### Acceptance tests (vol. 06) — status

| Acceptance test | Status |
|---|---|
| Cross-tenant access denied | ❌ phase 03 |
| Unapproved spend blocked | ❌ phase 07 |
| Repeated request does not duplicate external action | ❌ phase 05/06 (`idempotencyKey` is already required on `Command`) |
| Failed worker resumes safely | ❌ phase 06 |
| Prompt injection cannot elevate grants | ❌ phase 07/10 |
| Agent cannot spawn beyond quota | ❌ phase 12 |
| Cancellation prevents new tasks | ❌ phase 06 |
| Secrets absent from logs | 🟡 secrets absent from **repo** is tested now; log redaction in phase 10 |
| Browser session visible | ❌ phase 11 |
| Memory cites source | ❌ phase 09 |
| Sandbox Shopify draft verified | ❌ phase 15 |
| Website preview passes tests | ❌ phase 17 |
| Content remains draft until approved | ❌ phase 16 |

## 3. Architecture decisions (ADRs)

**ADR-001 — `Jaxon-core` is the Jackson OS repo.** The spec names `jaxon-os`; the only repo in scope is `STEADYLITT/Jaxon-core`. Its stated purpose ("core brain & memory") matches. Build here; rename later is optional and cheap.

**ADR-002 — Polyglot monorepo per vol. 06.** pnpm workspaces for TypeScript (`apps/*`, `packages/*`); each Python service has its own `pyproject.toml`. One CI workflow covers both. Rationale: spec layout, and console/contracts in TS with orchestration in Python.

**ADR-003 — FastAPI replaces Flask.** Flask was listed but never used. Spec mandates FastAPI (async, typed, OpenAPI out of the box). Root `requirements.txt` now points at `services/api`.

**ADR-004 — Contracts mirrored in TS and Python, enforced by a parity test.** `packages/contracts/src/index.ts` and `services/api/jackson_api/contracts.py` must match; `tests/test_repo.py` fails CI if they drift. In phase 05, switch to a single source (OpenAPI generated from FastAPI → TS types) and keep the parity test as a guard.

**ADR-005 — PostgreSQL (pgvector) is canonical; Redis is cache only.** Vector search supplements records, never replaces them (vol. 00). Local dev via `infra/docker-compose.yml`, bound to 127.0.0.1. Managed hosting decided later (vol. 02).

**ADR-006 — Temporal for durable workflows, decided at phase 06 gate.** Default per spec. If self-hosting Temporal proves too heavy for a single-owner pilot, fallback is Temporal Cloud; a Postgres-backed queue is the last resort and would require a new ADR because it gives up durable timers/replay.

**ADR-007 — Connector modes: `mock` → `sandbox` → `live`.** Every connector result carries its mode. Mocks are the default and must say "mock" in output and UI. Moving to `live` requires owner-provided credentials and an explicit approval. No connector may be called "integrated" while in mock mode (vol. 00 definition of done).

**ADR-008 — Growth OS is an import source, not a runtime dependency.** Jackson OS's schema is a superset of Growth OS. A one-way, idempotent importer will read a Base44 export, write records with provenance (`source_system = "base44_growth_os"`, `source_id`, `imported_at`), and never write back. Re-running an import must not duplicate rows. Mapping in §6.

**ADR-009 — Secrets never in Git, prompts or logs.** `.env.example` holds names only (test-enforced); real values go in a local `.env` (git-ignored) or a secret manager. CI scans tracked files for common key formats.

**ADR-010 — Tenancy: one owner tenant first, `tenant_id` on every row from day one.** Steady's businesses (Steady Trades University, CSW Trades, Steady Ventures, APEX-7, Shopify store) are `Business` rows under the owner tenant. Steady Ventures **client** work gets its own tenants (vol. 03: isolated client workspaces).

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
| Node | runtime | >=22.12 | |
| Python | runtime | >=3.12 (CI 3.12, local 3.13) | |

### Removed
`flask`, `requests` (unused; see ADR-003). `requests` will return only if a connector needs it; `httpx` is preferred.

### Infrastructure (local only, not deployed)
`pgvector/pgvector:pg16`, `redis:7-alpine` via docker-compose. Planned: Temporal (06), MinIO/S3 (artifacts), Playwright (11).

## 5. Credential inventory — names only, none provided

| Variable name | For | Needed by phase | Status |
|---|---|---|---|
| `POSTGRES_PASSWORD` | local DB | 04 | owner sets locally |
| `AUTH_SECRET` | session signing | 03 | not provided |
| `TEMPORAL_ADDRESS` | workflows | 06 | not provided |
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

1. **Auth provider choice** (phase 03): hosted (faster, monthly cost) vs. self-managed. Default if no answer: hosted, owner-only login.
2. **Hosting choice** (spec: after load/security tests) — not needed until phase 20.
3. **Growth OS export**: a Base44 data export (or API access) is needed to finalize §6 field mapping.
4. **Credentials** (§5): none needed until phase 08; nothing blocks phases 03–07.
5. **Repo name**: `Jaxon-core` vs spec `jaxon-os` — cosmetic, no action needed.
