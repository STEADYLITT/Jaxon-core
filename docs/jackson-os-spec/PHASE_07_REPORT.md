# Phase 07 — Policy, approvals, budget ledger

Date: 2026-11-25 · Decision: ADR-017 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Every action is checked first.** Before an agent spends money, posts publicly, emails people, takes payments, creates accounts or changes something live, Jackson checks it against that business's autonomy level (0–3, set only by you).
- **Always needs your okay:** public posts, outbound campaigns, payments, account creation, production changes and sensitive data, at any level.
- **Never allowed:** live trades (APEX-7 stays simulation-only).
- **Money:** spending runs on its own only at level 3 *and* within the run's budget. Anything else waits in your approvals inbox. Every cent is reserved before it's spent, so two tasks can't overspend at the same time.
- **Can't be talked out of it.** The AI can't approve its own requests or raise its own level, and text claiming "the owner already approved this" changes nothing. The database itself blocks it.

## What was built
| Piece | File |
|---|---|
| Pure rule engine (levels, always-approve, never, budget) | `jackson_api/policy.py` |
| Applies rules from DB facts; ledger reserve/settle; approval decisions | `jackson_api/governance.py` |
| Worker hooks: `ctx.authorize()`, `ctx.reserve_budget()`, `ctx.settle_spend()`; tasks that need approval wait as `waiting_approval` | `jackson_api/worker.py` |
| `GET /approvals`, `POST /approvals/{id}/decision`, `PUT /businesses/{id}/autonomy` (owner only), `GET /runs/{id}/ledger` | `jackson_api/routes_policy.py` |
| Tables `approvals`, `ledger_entries`, `businesses.autonomy_level`, with RLS and grants | `migrations/versions/0004_policy.py` |
| EXECUTE accepted when it names a business; every action inside still passes policy | `jackson_api/runs.py` |

Rules by level:
| Category | Runs without asking at |
|---|---|
| read | 0+ |
| draft, simulation | 1+ |
| sandbox change | 2+ |
| spend | 3, and only within the remaining budget |
| public post, outbound campaign, payment, account creation, production change, sensitive data | never; always asks |
| live trade | never; always refused |

Approval flow: the task asks → task and run become `waiting_approval` (not counted as a failed attempt) → owner/admin approves (task requeued, re-runs with the approval on file for that exact action) or rejects (task and run fail with the note).

## Tests
- **API (`services/api`): 150 passed** against real PostgreSQL 16 (60 new).
- `test_policy.py` (41): every category × level, always-approve, live trade never allowed, budget edge, negative amounts, approval tied to the exact action (fingerprint).
- `test_governance.py` (19), against the database:
  - spend within budget runs and settles; **unapproved spend is blocked**, then runs after approval
  - below level 3 asks; reject fails the run; a decided approval can't be decided again
  - **concurrent reservations can't overspend** (2 threads × 600 against a 1000 budget: exactly one wins); reservations are idempotent
  - public post asks even at level 3; live trade is refused with no approval created
  - **prompt injection can't elevate grants**: an injected objective and a fake tool output still end up waiting for approval
  - **agents can't approve themselves**: the worker role gets "permission denied" on UPDATE approvals, businesses and ledger
  - ledger append-only for the API; only the owner changes autonomy, and it's audited
  - viewers and other tenants can't decide; an approval on a canceled run can't be approved
  - EXECUTE with a business is accepted; approvals and ledger are tenant-isolated
- **Mutation checks** (all caught):
  - removing the run lock fails the overspend race 3/3 times
  - dropping the always-approve rule fails 26 tests
  - skipping the budget check fails 5 tests

## Spec acceptance tests now covered
- ✅ Unapproved spend blocked
- ✅ Prompt injection cannot elevate grants
- ✅ (vol. 05) No agent approves its own restricted action

## Next: phase 08 — model adapters (Claude, OpenAI, mock)
