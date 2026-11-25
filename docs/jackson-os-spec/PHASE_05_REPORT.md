# Phase 05 — Typed commands and run API

Date: 2026-11-25 · Builds on phases 03–04 (auth, tenant isolation). Decision: ADR-015 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Giving Jackson orders.** You (or the console) send a typed order such as PLAN, BUILD, REPORT or SIMULATE, with an objective, an optional business, a budget ceiling, a deadline, constraints and success criteria. Jackson records it as a **run** with a first "plan" step.
- **No double orders.** Every order carries a unique key. Sending the same order twice (a double-click or a network retry) returns the original run and creates nothing new. Reusing a key for a *different* order is refused.
- **Stop / pause / resume.** STOP (or the cancel button) ends a run, and an ended run can never get new work. PAUSE and RESUME tell workers to hold off or continue.
- **Live feed.** A live event stream shows each change to a run within a fraction of a second, so the dashboard can update without refreshing.
- **EXECUTE is locked.** It is the only order that would act on the outside world, so it stays refused until the approvals and budget system exists (phase 07).
- **Nothing runs yet.** Runs wait in the queue; workers that actually do the work come in phase 06.

## API (all under `/tenants/{tenant_id}`, login required)
| Method | Path | Who | Notes |
|---|---|---|---|
| POST | `/commands` | owner/admin | 201 new run; 200 + `Idempotent-Replay: true` on repeat; 409 key reused; 422 invalid / EXECUTE |
| GET | `/runs`, `/runs/{id}` | any member | run + its tasks |
| POST | `/runs/{id}/cancel` | owner/admin | idempotent; 409 if already succeeded/failed |
| GET | `/events?run_id=&after=&timeout=` | any member | Server-Sent Events; resumes from `Last-Event-ID` |

## State machine (`jackson_api/runs.py`)
`queued → running | canceled | failed` · `running → waiting_approval | succeeded | failed | canceled` · `waiting_approval → running | failed | canceled`. Terminal states (succeeded, failed, canceled) never change. A test pins the exact graph.

## Data (`migrations/versions/0002_runs.py`)
`workflow_runs` (unique per tenant + idempotency key, request hash, paused flag), `tasks`, `run_events` (append-only history whose id doubles as the SSE event id). All three tables have Postgres row-level security. The app role can't delete runs or tasks, and can't change or delete run history.

## From PR #1 (ADR-014)
Carried over as ideas, not code:
- a finished run is never executed again (now enforced by the state machine);
- every run keeps a step-by-step history (now `run_events`, streamed over SSE).

Not ported yet: PR #1's Redis queue and crash-recovering worker go in phase 06; its agent loop goes in phase 12.

## Tests
- **API (`services/api`): 66 passed**, against real PostgreSQL 16. 34 are new:
  - `test_runs.py`: create, list, get; invalid commands (empty objective, EXECUTE, STOP without target, negative budget, unknown type, another tenant's business)
  - idempotent replay creates no duplicate run, task or event; same key with a different body → 409; keys are per tenant
  - cancel cancels the run and its tasks and can be repeated; STOP behaves the same as cancel
  - **cancellation prevents new tasks**; pause and resume can be repeated; a finished run can't be paused
  - viewers can read but not command; cross-tenant runs, cancel, STOP and events are all denied, and RLS hides runs with no tenant scope
  - run history is append-only
  - SSE: events arrive in order, resume from `Last-Event-ID`, and never include another tenant's events
  - `test_state_machine.py`: terminal states, cancel from every live state, the exact transition graph
- **Repo (`tests/`): 6 passed.** New: the TS `WorkflowRun`/`TaskOut`/`Command` types must match the API's wire format.
- **Mutation checks:**
  - turning off the "different request under the same key" check fails `test_same_key_different_request_conflicts`
  - allowing tasks on a finished run fails `test_cancellation_prevents_new_tasks`
- **Real-server run:**
  - uvicorn with a local stand-in identity provider: the real JWKS key-download path, tested for the first time
  - seed, then `POST /commands` → 201, same key → 200
  - with `curl -N` open on `/events`, a cancel showed up on the stream about 40 ms after the request
  - no token → 401

## Spec acceptance tests now covered
- ✅ Repeated request does not duplicate external action (at the command layer; external connectors must also forward idempotency keys when they arrive).
- ✅ Cancellation prevents new tasks.
- ✅ Cross-tenant access denied (now including runs and events).

## Blockers
None for phase 06. Real logins still need the Clerk addresses and hosting (see PHASE_03_04_REPORT).

## Next: phase 06 — durable workflow engine
Workers that pick up queued, unpaused runs and keep going safely after a crash ("failed worker resumes safely"). This is where the Temporal vs. Postgres-queue decision (ADR-006) gets made, using PR #1's Redis worker as the reference.
