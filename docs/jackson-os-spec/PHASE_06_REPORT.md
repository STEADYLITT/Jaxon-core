# Phase 06 — Durable workflow engine (Postgres-backed queue)

Date: 2026-11-25 · Decision: ADR-016 (supersedes ADR-006) in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Workers now actually do the work.** Queued runs get picked up and processed.
- **Crash-safe:** if a worker dies mid-task, another worker takes the task over automatically, and the dead worker can't overwrite the result later.
- **Retries:** temporary errors are retried with growing waits. After 3 failures the task is set aside with its error saved, and the run is marked failed.
- **Stop and pause work at execution time:** a canceled run's work is never started, late results are thrown away and logged, and paused runs wait.
- **Fair sharing:** one workspace can't take every worker.
- **No AI yet:** the "plan" step is a clearly labeled MOCK until the model connection arrives (phase 08).

## Decision: Postgres queue, not Temporal (ADR-016)
Postgres is already the system of record, so the queue lives in the same database and the same transactions. There is no extra server to run or pay for, and no second source of truth. Claims use `SELECT … FOR UPDATE SKIP LOCKED` with time-limited **leases**.

Revisit Temporal if we need long timers or human waits lasting weeks, many-step sagas, or more than roughly 50 task claims per second.

## How it works (`services/api/jackson_api/worker.py`)
| Mechanism | Detail |
|---|---|
| Claim | Locks the next due task (`SKIP LOCKED`); skips paused, finished and over-quota tenants; moves the run queued→running |
| Lease + heartbeat | 60 s lease, extended by `ctx.heartbeat()`. Results are saved only if this worker still holds the lease |
| Retry | Exponential backoff (5 s, 10 s, … capped at 300 s), up to `max_attempts` (3) |
| Dead letter | Task is kept as `failed` with its error; `task.dead_lettered` event and audit entry; run failed, sibling tasks canceled |
| Crash recovery | Expired leases are reaped, so the task is requeued (or dead-lettered if out of attempts) |
| Idempotency | `ctx.idempotency_key = "<run_id>:<sequence>"`, the same on every retry, for connectors to forward |
| Quota | `WORKER_TENANT_CONCURRENCY` (default 4) running tasks per tenant |
| Shutdown | SIGTERM/SIGINT stop the loop after the current task |

**Least privilege:** the worker connects as `jackson_worker`, which sees queue tables across tenants (it must find work everywhere). It can't delete anything, can't read users or memberships, and can only append to history. Migration `0003_worker`.

From PR #1 (ADR-014), carried over as ideas: graceful signal shutdown, and a worker id built from the host name. PR #1's per-worker recovery list became the lease approach, so any worker can recover any crashed task.

## Tests
- **API (`services/api`): 90 passed** against real PostgreSQL 16. 24 are new, in `test_worker.py`:
  - mock plan reaches `succeeded` and is labeled mock; follow-up tasks keep the run open; an idle worker does nothing
  - retries with backoff (a task that isn't due yet isn't claimed); dead letter after 3 attempts; a non-retryable error fails at once; an unknown task kind is dead-lettered
  - **failed worker resumes safely**; a zombie worker can't overwrite results while the new owner is working; heartbeat
  - 4 threads competing for 6 tasks never claim the same one twice
  - a canceled run is never started; cancel mid-task discards the result (logged) and adds no follow-ups; pause, then resume
  - per-tenant quota; worker role can't delete, edit history or read users/memberships
- **Mutation checks** (both caught):
  - removing the lease-owner check fails the zombie test. A first version of the tests missed this; the gap was found and fixed.
  - ignoring pause fails the pause test
- **Real processes:** migrate 0001→0003, then API + `python -m jackson_api.worker` + local stand-in issuer. A command reached `succeeded`, SSE showed all 6 events, and the worker exited cleanly on SIGTERM.

## Spec acceptance tests now covered
- ✅ Failed worker resumes safely
- ✅ Cancellation prevents new tasks (now also at execution time)

## Next: phase 07 — policy, approvals, budget ledger
