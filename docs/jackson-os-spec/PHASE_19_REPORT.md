# Phase 19 — Portfolio dashboard and reporting

Date: 2026-11-26 · Decision: ADR-029 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **You now have one screen for everything.** It shows:
  - money per business: real revenue, refunds, spend, AI cost and real profit
  - your estimates, in their own column and never mixed into profit
  - the opportunity board and the agent roster
  - recent runs with their cost
  - the approvals inbox, with approve/reject buttons
  - live browser work, files Jackson made, which connections are live versus stand-ins, and the audit log
- **Honest numbers.** If something isn't measured yet (conversion rate, customer acquisition cost), it says "not measured" and why, never 0%.
- **Alerts** for:
  - repeated failures
  - approvals waiting more than a day
  - AI spend near the monthly budget
  - outreach messages that went to the sender but weren't confirmed
  - a live connection that's gone quiet
- **The big red GLOBAL STOP button:**
  - cancels all running work, unschedules posts and cancels campaigns
  - stops anything new starting, enforced by the database itself
  - gives you a list of what may already have gone out, so you can check it
  - live products and websites stay up; taking those down is a separate, approved step
  - Resume turns work back on.
- **A daily report** of what got done, what's blocked and what needs you next, saved each time.
- **Where it lives:** the console is at `/console` on the API for now. You paste a sign-in token. The proper hosted console with Clerk sign-in replaces it once Clerk is set up.

## What was built
| Piece | File |
|---|---|
| Dashboard aggregation: P&L, metrics, alerts, connectors, timeline, roster, approvals, live work, artifacts, audit; global stop + reconcile; resume; daily report + handler | `jackson_api/dashboard.py` |
| Global-stop columns on `tenants`; append-only `revenue_entries`; DB trigger refusing new runs and task starts while stopped | `models.py`, `migrations/versions/0016_dashboard.py` |
| Stop checks in run creation and in the worker's claim query | `runs.py`, `worker.py` |
| API endpoints | `jackson_api/routes_dashboard.py` |
| Interim console (no inline script, strict CSP, all data written as text) | `jackson_api/console/` |

The API endpoints:
- `GET /dashboard`
- `POST /stop` and `POST /resume` (owner only)
- `POST /revenue` and `GET /revenue`
- daily report: `POST /reports/daily`, `GET /reports/daily/preview`, `GET /reports`
- `GET /console`

## Tests
- **API (`services/api`): 391 passed** against real PostgreSQL 16, 12 of them new in `test_dashboard.py`.
- **Dashboard:**
  - an empty dashboard shows "no data" rather than fake zeros, and every connector is labeled MOCK with what it needs
  - P&L per business is exact: actual revenue, refunds, committed spend, money held, model cost and actual profit, **with estimates kept separate**
  - actual revenue needs a reference, and revenue entries can't be edited or deleted
  - success rate, failure rate and latency are correct
  - the repeated-failure and stale-approval alerts fire
- **Global stop:**
  - cancels queued runs, unschedules posts, cancels campaigns and returns the reconcile list
  - new commands get 423
  - **the database refuses a new run or a task start, even bypassing the API**
  - the worker skips stopped tenants
  - resume works and both actions are audited
  - only the owner can stop: viewers and admins get 403
- **Daily report:** stored as an artifact with Outcomes, Blockers, Next actions and Numbers sections.
- **Tenant isolation:** another tenant gets 403 on the dashboard, revenue, reports and stop.
- **Real browser:**
  - the console signs in, shows the dashboard, and the stop/resume buttons work end to end against the real API
  - a run named `<img onerror=…>` is shown as text and never runs
  - the page's own security policy blocks injected script
- **Mutation checks (12/12 caught):**
  - estimates added to profit
  - no stop check in run creation
  - worker not skipping stopped tenants
  - DB trigger off
  - owner-only check removed
  - runs not cancelled
  - campaigns not cancelled
  - content left scheduled
  - innerHTML instead of textContent
  - "no data" shown as 0
  - actual revenue without a reference
  - console asset path check removed (this one first slipped through; I added a direct test)

## Spec items covered (vol. 04)
**Dashboard:**
- Portfolio P&L, cash and committed spend ✅
- Opportunity board ✅
- Agent roster and health ✅
- Workflow timeline ✅
- Live visual work ✅
- Approvals inbox ✅
- Artifacts ✅
- Connector status ✅
- Audit logs ✅
- Global stop with reconcile ✅
- Actual and estimated revenue kept separate ✅

**Metrics:** cost per run, verified success, failure rate, latency, approval wait, gross margin and refunds ✅. Conversion and CAC need a storefront/analytics connector.

**Alerts and daily reports** ✅

**First vertical slice:** "dashboard shows artifact, cost and run history" ✅

## Owner actions
1. Use `/console` now with a session token.
2. For the proper hosted console with Clerk sign-in, provide the Clerk settings (`CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `AUTH_*`).
3. To have the daily report arrive on its own, we can schedule it once hosting is picked (phase 20).

## Next: phase 20 — security, recovery and end-to-end pilot
