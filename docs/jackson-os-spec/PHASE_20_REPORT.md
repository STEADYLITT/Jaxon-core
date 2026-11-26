# Phase 20 — Security, recovery and end-to-end pilot

Date: 2026-11-26 · Decision: ADR-030 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs). Runbooks: [`docs/runbooks/`](../runbooks/).

## Plain English
- **Locked doors are checked automatically.** A test walks every one of the API's routes and proves each refuses anyone not signed in, except a short, reviewed list of public pages (health check, the console page itself, unsubscribe links). Any future route that forgets sign-in fails the build.
- **It won't start unsafely.** In production mode Jackson refuses to boot unless the basics are right:
  - it uses limited database accounts, not the all-powerful one
  - sign-in is set up, over https
  - the secrets are long enough
  - files are stored somewhere permanent
  - risky code-running is switched off

  `python -m jackson_api.preflight` shows the checklist. It prints names, never secret values.
- **Standard web protections are on:** browsers are told not to cache your data, not to frame the site, and not to guess file types. Oversized requests are rejected and the public unsubscribe page is rate-limited.
- **Backups you can trust.** One command backs up the database and files with checksums. Restore checks the backup isn't corrupted and refuses to overwrite a live database. Afterwards it proves:
  - the data came back
  - the security came back: tenant walls, the safety triggers, the append-only audit log
  - a restore that lost its security is flagged as failed

  CI does a full backup → restore → verify on every change.
- **The first business flow runs end to end, automatically**, on every change:
  1. research with cited sources
  2. margin maths
  3. your approval
  4. a draft product on the stand-in store
  5. QA
  6. the dashboard shows the report, the AI cost and the run history
  7. the daily report summarises it
  8. the global stop halts it
- **Runbooks** in plain steps: backups and restore, "something's wrong, hit stop", and how to run the first real pilot once you provide the accounts.

## What was built
| Piece | File |
|---|---|
| Security middleware: headers, no-store for tenant data, HSTS in production, body limit, public rate limit; production preflight + fail-closed start-up | `jackson_api/security.py`, `jackson_api/preflight.py`, `main.py` |
| `code.run_python` disabled in production (no container sandbox yet) | `jackson_api/tools/builtin.py` |
| Backup (pg_dump custom + artifacts + counts + SHA-256) and restore (checksum + empty-target guard + verification) | `infra/backup/backup.sh`, `infra/backup/restore.sh` |
| Restore verification: schema head, RLS + policies, guard triggers, append-only grants, row counts | `jackson_api/verify_restore.py` |
| Exact model cost on the dashboard (sub-cent calls no longer show as $0.00) | `jackson_api/dashboard.py`, console |
| CI: PostgreSQL 16 client tools for the recovery tests | `.github/workflows/ci.yml` |
| Runbooks: backup/restore, incident + global stop, pilot | `docs/runbooks/` |

## Tests
- **API (`services/api`): 413 passed** against real PostgreSQL 16, 22 of them new: 18 security, 3 recovery and 1 end-to-end pilot. **Repo guardrails: 6 passed.**
- **Security:**
  - every route needs sign-in except the reviewed public list
  - the security headers are present, and the console's own CSP is kept
  - 413 for oversized bodies; 429 after the public rate limit
  - the preflight passes a safe production config and catches each of 8 unsafe settings
  - production refuses to start with an unsafe config, and starts with a safe one while hiding the docs and sending HSTS
  - the preflight never prints secrets
  - code execution is off in production
- **Recovery:**
  - the backup → restore → verify round trip includes artifacts, and tenant isolation still holds in the restored database
  - corrupted backups and non-empty targets are refused
  - verification catches a restore that lost RLS, a trigger or the append-only grants
- **Pilot:** the first vertical slice runs end to end through the API and worker (described above).
- **Mutation checks (13/13 caught):**
  - body limit, rate limit, nosniff, no-store
  - fail-closed start-up, docs hiding
  - DB-role check, artifact-storage check
  - code execution in production
  - verifier RLS check, verifier trigger check
  - backup checksum
  - non-empty restore target. This one first slipped through, because the restore failed for a different reason. The test now checks the specific refusal.

## The spec's acceptance list — all 13 covered
| Acceptance test | Where |
|---|---|
| Cross-tenant access denied | phase 03, and re-proved on a restored backup (20) |
| Unapproved spend blocked | phase 07 |
| Repeated request does not duplicate external action | phases 05, 15, 18 |
| Failed worker resumes safely | phase 06 |
| Prompt injection cannot elevate grants | phases 07, 10 |
| Agent cannot spawn beyond quota | phase 12 |
| Cancellation prevents new tasks | phase 05; global stop (19) |
| Secrets absent from logs | phases 02, 08, 10; preflight output (20) |
| Browser session visible | phase 11 |
| Memory cites source | phase 09 |
| Sandbox Shopify draft verified | phase 15; pilot (20) |
| Website preview passes tests | phase 17 |
| Content remains draft until approved | phase 16 |

## Known limits (open items)
- **Rate limit:** the rate limiter is per process; use the host's edge rate limiting too.
- **Code sandbox:** no container sandbox for untrusted code yet, so `code.run_python` is off in production.
- **Dependency scanning:** dependency vulnerability scanning isn't in CI yet. It needs a decision on blocking versus advisory.
- **Hosted console:** the Clerk-hosted console (`apps/console`) is still to build. The interim `/console` covers the dashboard functions.
- **Mock connectors:** these connectors are clearly labeled mocks until accounts exist: social posting, hosting, email/SMS, and real Shopify/Claude.

## What the owner needs to provide for the real pilot
See [`docs/runbooks/pilot.md`](../runbooks/pilot.md):
- Clerk settings
- `ANTHROPIC_API_KEY`
- a Shopify **development** store + Admin API token
- 1–3 product pages to research
- a hosting choice
