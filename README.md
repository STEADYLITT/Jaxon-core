# Jaxon Core

This repo holds the core brain & memory system for Jaxon Vale — AI partner.

It is the codebase for **Jackson OS**, the long-term software version of Jackson (v1 lives in the Base44 app *Steady Growth OS*; its data will be imported later — see ADR-008).

## Status
Phases 01–04 of 20: inventory, scaffold + CI, hosted-provider auth + tenant isolation, Postgres + migrations. **No external integrations are live; nothing is deployed.**

- Spec: [`docs/jackson-os-spec/`](docs/jackson-os-spec/START_HERE.md)
- Gap report, ADRs, dependency & credential inventory: [`PHASE_01_GAP_REPORT.md`](docs/jackson-os-spec/PHASE_01_GAP_REPORT.md)
- Auth + database (setup steps): [`PHASE_03_04_REPORT.md`](docs/jackson-os-spec/PHASE_03_04_REPORT.md)

## Layout
```
apps/console          owner console (Next.js) — not built
services/api          FastAPI API — auth, tenants, businesses, audit log, migrations
services/workers      agent workers — not built
packages/contracts    shared TS types (mirrored in services/api, parity-tested)
packages/policy       autonomy/approvals/budgets — not built
packages/connectors   external connectors — mocks only, none live
skills/ agents/ workflows/   registries — not built
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

## Rules
- Never commit secrets. `.env.example` lists variable **names only**.
- Connectors are labeled mocks until the owner provides credentials and approves.
- No push to `main` without review.
