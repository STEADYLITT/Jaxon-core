"""Phase 06 acceptance: durable execution on the Postgres queue.

Spec vol. 06 acceptance covered here:
- "failed worker resumes safely" (lease expiry -> another worker finishes the task)
- "cancellation prevents new tasks" (now also at execution time)
- retries with backoff, dead-lettering, pause, per-tenant quota, no double claims
"""

import threading

import pytest
from conftest import auth, events, fast_worker, new_run, run_state
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from jackson_api.db import bind_tenant
from jackson_api.models import Task, WorkflowRun
from jackson_api.worker import NonRetryableError, TaskResult, Worker

# --- Happy path ---------------------------------------------------------------------

def test_default_mock_plan_runs_to_success_and_is_labeled_mock(
        client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    assert Worker(maker).run_once() is True
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and run.started_at and run.finished_at
    assert tasks[0].status == "succeeded"
    assert tasks[0].output["mode"] == "mock"
    assert events(owner_db, run_id)[-3:] == ["task.started", "task.succeeded",
                                            "run.state_changed"]


def test_follow_up_tasks_keep_run_open_until_all_done(
        client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    handlers = {"plan": lambda ctx: TaskResult({"ok": 1}, follow_ups=[("research", "Research")]),
                "research": lambda ctx: TaskResult({"ok": 2})}
    w = fast_worker(maker, handlers)
    w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "running" and [t.kind for t in tasks] == ["plan", "research"]
    w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and {t.status for t in tasks} == {"succeeded"}


def test_idle_worker_does_nothing(maker, drain):
    assert Worker(maker).run_once() is False


def test_handler_gets_stable_idempotency_key(client, world, make_token, maker, drain):
    seen = []
    run_id = new_run(client, world, make_token)

    def flaky(ctx):
        seen.append(ctx.idempotency_key)
        if len(seen) == 1:
            raise RuntimeError("transient")
        return TaskResult({})

    w = fast_worker(maker, {"plan": flaky})
    w.run_once()
    w.run_once()
    assert seen == [f"{run_id}:1", f"{run_id}:1"]


# --- Retries and dead letters --------------------------------------------------------

def test_transient_failure_retries_then_succeeds(
        client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    calls = []

    def flaky(ctx):
        calls.append(ctx.attempt)
        if len(calls) < 3:
            raise ConnectionError("upstream timeout")
        return TaskResult({"ok": True})

    w = fast_worker(maker, {"plan": flaky})
    for _ in range(3):
        w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert calls == [1, 2, 3]
    assert run.status == "succeeded" and tasks[0].attempts == 3
    assert events(owner_db, run_id).count("task.retry_scheduled") == 2


def test_backoff_delays_the_retry(client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    w = Worker(maker, handlers={"plan": lambda ctx: 1 / 0}, retry_base_seconds=3600)
    w.run_once()
    _, tasks = run_state(owner_db, run_id)
    assert tasks[0].status == "queued" and tasks[0].next_attempt_at is not None
    assert w.run_once() is False  # not due yet


def test_exhausted_retries_dead_letter_and_fail_run(
        client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    w = fast_worker(maker, {"plan": lambda ctx: 1 / 0})
    for _ in range(5):
        w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "failed"
    assert tasks[0].status == "failed" and tasks[0].attempts == 3
    assert "ZeroDivisionError" in tasks[0].error
    assert "task.dead_lettered" in events(owner_db, run_id)


def test_non_retryable_error_fails_immediately(
        client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)

    def bad(ctx):
        raise NonRetryableError("objective is not a product")

    fast_worker(maker, {"plan": bad}).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "failed" and tasks[0].attempts == 1


def test_unknown_task_kind_is_dead_lettered(client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    fast_worker(maker, {}).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "failed" and "no handler" in tasks[0].error


# --- Crash recovery -------------------------------------------------------------------

def test_failed_worker_resumes_safely(client, world, make_token, maker, owner_db, drain):
    """Worker A claims the task and dies. After its lease expires, worker B finishes it,
    and A's late result is thrown away."""
    run_id = new_run(client, world, make_token)
    a = fast_worker(maker, {"plan": lambda ctx: TaskResult({"by": "A"})}, worker_id="A")
    b = fast_worker(maker, {"plan": lambda ctx: TaskResult({"by": "B"})}, worker_id="B")
    task_id = a.claim()  # A claims, then "crashes" before finishing
    assert task_id is not None
    owner_db.execute(update(Task).where(Task.id == task_id)
                     .values(lease_expires_at=func.now() - text("interval '1 second'")))
    owner_db.commit()
    assert b.run_once() is True  # reaps A's lease, then claims and runs it
    a._record_success(task_id, TaskResult({"by": "A (late)"}))  # zombie result
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded"
    assert tasks[0].output == {"by": "B"} and tasks[0].attempts == 2
    assert "task.lease_expired" in events(owner_db, run_id)


def test_heartbeat_keeps_lease_alive(client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    beats = []

    def long_task(ctx):
        beats.append(ctx.heartbeat())
        return TaskResult({})

    fast_worker(maker, {"plan": long_task}).run_once()
    run, _ = run_state(owner_db, run_id)
    assert beats == [True] and run.status == "succeeded"


def test_two_workers_never_claim_the_same_task(client, world, make_token, maker, drain):
    for _ in range(6):
        new_run(client, world, make_token)
    claimed: list = []
    lock = threading.Lock()

    def grab(name):
        w = fast_worker(maker, {}, worker_id=name, tenant_concurrency=100)
        while (tid := w.claim()) is not None:
            with lock:
                claimed.append(tid)

    threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 6 and len(set(claimed)) == 6


# --- Cancel / pause / quota -------------------------------------------------------------

def test_canceled_run_is_never_started(client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    client.post(f"/tenants/{world.tenant_a}/runs/{run_id}/cancel",
                headers=auth(make_token, world.alice))
    started = []
    fast_worker(maker, {"plan": lambda ctx: started.append(1) or TaskResult({})}).run_once()
    assert started == []


def test_cancel_during_execution_discards_result_and_blocks_follow_ups(
        client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    h = auth(make_token, world.alice)

    def cancel_midway(ctx):
        client.post(f"/tenants/{world.tenant_a}/runs/{run_id}/cancel", headers=h)
        assert ctx.should_stop() is True
        assert ctx.heartbeat() is False
        return TaskResult({"late": True}, follow_ups=[("build", "Build store")])

    fast_worker(maker, {"plan": cancel_midway}).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "canceled"
    assert [t.kind for t in tasks] == ["plan"] and tasks[0].status == "canceled"
    assert tasks[0].output is None
    assert "task.result_discarded" in events(owner_db, run_id)


def test_paused_run_is_skipped_until_resumed(client, world, make_token, maker, owner_db, drain):
    run_id = new_run(client, world, make_token)
    h = auth(make_token, world.alice)
    url = f"/tenants/{world.tenant_a}/commands"
    client.post(url, headers=h, json={"type": "PAUSE", "target_run_id": run_id,
                                      "idempotency_key": "pause-0001"})
    w = fast_worker(maker, {"plan": lambda ctx: TaskResult({})})
    assert w.run_once() is False
    client.post(url, headers=h, json={"type": "RESUME", "target_run_id": run_id,
                                      "idempotency_key": "resume-0001"})
    assert w.run_once() is True
    assert run_state(owner_db, run_id)[0].status == "succeeded"


def test_tenant_concurrency_quota(client, world, make_token, maker, drain):
    for _ in range(3):
        new_run(client, world, make_token)
    w = fast_worker(maker, {}, tenant_concurrency=2)
    assert w.claim() is not None
    assert w.claim() is not None
    assert w.claim() is None  # tenant A is at its quota of 2 running tasks
    new_run(client, world, make_token, tenant=world.tenant_b, who=world.bob)
    assert w.claim() is not None  # tenant B still gets served


# --- Least privilege for the worker role ---------------------------------------------------

@pytest.mark.parametrize("stmt", [
    "DELETE FROM tasks", "DELETE FROM workflow_runs", "DELETE FROM run_events",
    "UPDATE run_events SET type = 'x'", "SELECT * FROM users", "SELECT * FROM memberships",
])
def test_worker_role_is_least_privilege(maker, stmt):
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text(stmt))


def test_app_role_still_cannot_see_other_tenants_runs(
        client, world, make_token, app_session, drain):
    new_run(client, world, make_token, tenant=world.tenant_b, who=world.bob)
    bind_tenant(app_session, world.tenant_a)
    tenants = set(app_session.scalars(select(WorkflowRun.tenant_id)))
    assert world.tenant_b not in tenants


def test_zombie_worker_cannot_overwrite_while_new_owner_is_working(
        client, world, make_token, maker, owner_db, drain):
    """A's lease expires and B takes over. While B is still working, A wakes up and tries
    to save its result. A must be refused: B owns the lease now."""
    run_id = new_run(client, world, make_token)
    a = fast_worker(maker, {}, worker_id="A")
    b = fast_worker(maker, {}, worker_id="B")
    task_id = a.claim()
    owner_db.execute(update(Task).where(Task.id == task_id)
                     .values(lease_expires_at=func.now() - text("interval '1 second'")))
    owner_db.commit()
    assert b.reap_expired_leases() == 1
    assert b.claim() == task_id  # B now owns the running task
    a._record_success(task_id, TaskResult({"by": "A (zombie)"}))
    a._record_failure(task_id, RuntimeError("zombie failure"))
    _, tasks = run_state(owner_db, run_id)
    assert tasks[0].status == "running" and tasks[0].lease_owner == "B"
    assert tasks[0].output is None and tasks[0].error is not None  # error is from the reap
    assert "zombie" not in (tasks[0].error or "")
    b._record_success(task_id, TaskResult({"by": "B"}))
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and tasks[0].output == {"by": "B"}
