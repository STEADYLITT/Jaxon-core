# Phases 03–04 — Auth, tenant isolation, database + migrations

Date: 2026-11-25 · Decisions: ADR-012 (hosted auth), ADR-013 (two-layer isolation) in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs)

## Plain English
- **Login:** Steady will sign in through a hosted login service (Clerk recommended). Jackson OS never stores passwords. It checks a signed "ID badge" (token) on every request and rejects anything forged, expired, or issued for another site.
- **Walls between businesses and clients:** every record belongs to a tenant (a walled-off workspace). Two separate locks keep tenants apart. The API checks you're a member, and the database itself refuses to show or accept another tenant's rows even if the code has a bug.
- **Paper trail:** creating a business writes an audit entry the app cannot edit or delete.
- **Setup script:** one command creates Steady's workspace and five businesses: Steady Trades University, CSW Trades, Steady Ventures, APEX-7 (marked simulation-only) and the Shopify store.
- **Not live yet:** no Clerk account is connected. Tests use a fake test-only issuer and keys generated on the fly.

## What was built
| Piece | File |
|---|---|
| Token verification (RS256 + JWKS, iss/exp/nbf/sub, optional aud, `azp` allow-list) | `services/api/jackson_api/auth.py` |
| Deny-by-default dependencies (503 / 401 / 403 / 404) | `services/api/jackson_api/deps.py` |
| Endpoints: `GET /me`, `GET/POST /tenants/{id}/businesses`, `GET /tenants/{id}/businesses/{bid}`, `GET /tenants/{id}/audit-events`, `GET /ready` | `routes.py`, `main.py` |
| Tables: tenants, users, memberships, businesses, audit_events | `models.py` |
| Migration with app role, grants, RLS policies | `services/api/migrations/versions/0001_initial.py` |
| Tenant-scoped transactions | `db.py` (`bind_tenant`) |
| Owner bootstrap (idempotent) | `seed.py` |
| CI: real Postgres service for tests | `.github/workflows/ci.yml` |

## Local setup
```bash
cp .env.example .env              # set POSTGRES_PASSWORD, DATABASE_URL, MIGRATION_DATABASE_URL
docker compose -f infra/docker-compose.yml --env-file .env up -d
cd services/api && alembic upgrade head          # uses MIGRATION_DATABASE_URL
psql "$MIGRATION_DATABASE_URL" -c "ALTER ROLE jackson_app LOGIN PASSWORD '<choose one>'"
python -m jackson_api.seed --email <owner email> --auth-subject <Clerk user id>
```

## Tests (32 API + 5 repo)
`services/api/tests/test_auth.py` covers forged signature, expired, wrong issuer, wrong/missing `azp`, `alg=none` and HS256 key-confusion. All are rejected.

`services/api/tests/test_tenancy.py` runs against real Postgres:
- no token → 401; garbage → 401; unknown user → 403; auth unconfigured → 503
- cross-tenant list → 403; cross-tenant object id → 404; cross-tenant create → 403; viewer write → 403
- owner create → 201 plus an audit event; another tenant can't read that audit trail
- RLS directly: nothing visible without scope; only the bound tenant is visible even with no WHERE clause; inserts into another tenant are blocked; the scope ends with the transaction
- audit log UPDATE/DELETE → permission denied; app role is not superuser and has no BYPASSRLS
- migrations match models (no drift); seed is idempotent

Mutation check: setting the RLS policy to `true` makes the four RLS tests fail. The API-layer tests still pass, which shows the two locks are independent.

## Blockers / owner actions
1. **Create a Clerk application** (or tell me to use another provider). Then put the issuer, JWKS URL and the console origin in `.env` / the secret store. Nothing is needed in chat.
2. **Confirm Clerk's details:** clerk.com was unreachable from the build machine, so Clerk's token claims are not verified against current docs (ADR-012).
3. Your Clerk user id is needed to run the seed for real.

## Next: phase 05 — typed commands and run API
`POST /commands` (with idempotency key), `GET /runs/{id}`, `POST /runs/{id}/cancel`, `GET /events` (SSE), plus the WorkflowRun/Task tables, all tenant-scoped through the same two layers.
