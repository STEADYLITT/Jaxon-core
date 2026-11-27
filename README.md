# Jaxon Core

This repo holds the core brain & memory system for Jaxon Vale — AI partner.

It is the codebase for **Jackson OS**, the long-term software version of Jackson (v1 lives in the Base44 app *Steady Growth OS*; its data will be imported later — see ADR-008).

## Status
Phases 01–20 of 20: inventory, scaffold + CI, hosted-provider auth + tenant isolation, Postgres + migrations, typed commands + run API (SSE), durable worker on a Postgres queue, policy + approvals + budget ledger, model adapters (Claude default; MOCK until a key is set), memory with provenance, tool registry + gateway, watched browser sessions, agent registry + factory, opportunity research, product economics, Shopify drafts (mock until a dev-store token is set), content + virtual influencer workflow (posting mock until a social key is set), website factory (hosting mock until a hosting account exists), CRM + compliant outreach (sending mock until an email/SMS provider key is set), owner dashboard + global stop + daily reports (interim console at `/console`), security baseline, verified backup/restore and an end-to-end pilot test. **No external integrations are live; nothing is deployed.** To put it online, follow [`docs/DEPLOY.md`](docs/DEPLOY.md) (Railway, ~$15/month + capped AI).

- Spec: [`docs/jackson-os-spec/`](docs/jackson-os-spec/START_HERE.md)
- Gap report, ADRs, dependency & credential inventory: [`PHASE_01_GAP_REPORT.md`](docs/jackson-os-spec/PHASE_01_GAP_REPORT.md)
- Auth + database (setup steps): [`PHASE_03_04_REPORT.md`](docs/jackson-os-spec/PHASE_03_04_REPORT.md)
- Commands + runs API: [`PHASE_05_REPORT.md`](docs/jackson-os-spec/PHASE_05_REPORT.md)
- Worker / durable queue: [`PHASE_06_REPORT.md`](docs/jackson-os-spec/PHASE_06_REPORT.md)
- Policy, approvals, budget: [`PHASE_07_REPORT.md`](docs/jackson-os-spec/PHASE_07_REPORT.md)
- Model adapters: [`PHASE_08_REPORT.md`](docs/jackson-os-spec/PHASE_08_REPORT.md)
- Memory: [`PHASE_09_REPORT.md`](docs/jackson-os-spec/PHASE_09_REPORT.md)
- Tools: [`PHASE_10_REPORT.md`](docs/jackson-os-spec/PHASE_10_REPORT.md)
- Browser sessions: [`PHASE_11_REPORT.md`](docs/jackson-os-spec/PHASE_11_REPORT.md)
- Agents: [`PHASE_12_REPORT.md`](docs/jackson-os-spec/PHASE_12_REPORT.md)
- Opportunities: [`PHASE_13_REPORT.md`](docs/jackson-os-spec/PHASE_13_REPORT.md)
- Economics: [`PHASE_14_REPORT.md`](docs/jackson-os-spec/PHASE_14_REPORT.md)
- Shopify drafts: [`PHASE_15_REPORT.md`](docs/jackson-os-spec/PHASE_15_REPORT.md)
- Content + virtual influencer: [`PHASE_16_REPORT.md`](docs/jackson-os-spec/PHASE_16_REPORT.md)
- Website factory: [`PHASE_17_REPORT.md`](docs/jackson-os-spec/PHASE_17_REPORT.md)
- CRM + compliant outreach: [`PHASE_18_REPORT.md`](docs/jackson-os-spec/PHASE_18_REPORT.md)
- Owner dashboard: [`PHASE_19_REPORT.md`](docs/jackson-os-spec/PHASE_19_REPORT.md)
- Launch approvals: [`PHASE_21_LAUNCH_APPROVALS.md`](docs/jackson-os-spec/PHASE_21_LAUNCH_APPROVALS.md)
- Command-center console: [`PHASE_22_COMMAND_CENTER.md`](docs/jackson-os-spec/PHASE_22_COMMAND_CENTER.md)
- Security, recovery, pilot: [`PHASE_20_REPORT.md`](docs/jackson-os-spec/PHASE_20_REPORT.md) · runbooks in [`docs/runbooks/`](docs/runbooks/)

## Layout
```
apps/console          owner console (Next.js) — not built
services/api          FastAPI API — auth, tenants, businesses, audit log, commands/runs/SSE, migrations
services/workers      reserved; the worker currently lives in services/api (`python -m jackson_api.worker`)
packages/contracts    shared TS types (mirrored in services/api, parity-tested)
packages/policy       reserved; policy engine lives in services/api (`policy.py`, `governance.py`)
packages/connectors   external connectors — mocks only, none live
skills/ workflows/    registries — not built (agents live in the DB registry, phase 12)
infra/                local docker-compose (Postgres+pgvector, Redis)
tests/                repo-level guardrail tests
docs/                 spec, ADRs
```

## Develop
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pnpm install
```

## Test
```bash
ruff check services/api tests
# API tests need a scratch PostgreSQL (superuser URL):
export JACKSON_TEST_ADMIN_URL=postgresql+psycopg://postgres:<pw>@localhost:5432/postgres
(cd services/api && pytest)
pytest tests
pnpm typecheck && pnpm test
```

## Run the API locally
```bash
uvicorn jackson_api.main:app --app-dir services/api --reload
```

## Run the worker locally
```bash
WORKER_DATABASE_URL=postgresql+psycopg://jackson_worker:<pw>@localhost:5432/jackson python -m jackson_api.worker
```

## Rules
- Never commit secrets. `.env.example` lists variable **names only**.
- Connectors are labeled mocks until the owner provides credentials and approves.
- No push to `main` without review.
