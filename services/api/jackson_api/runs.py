"""Typed commands -> workflow runs (phase 05).

Rules:
- Every command carries an idempotency key. The same key with the same payload
  returns the original run; the same key with a different payload is a conflict.
- Run state changes only through `transition()`; terminal states never change.
- A canceled (or otherwise finished) run never receives new tasks.
- EXECUTE must name a business; its autonomy level is the granted scope, and every
  action in the run passes the policy engine (phase 07, governance.py).
- Nothing here executes work. Workers pick up queued, unpaused runs in phase 06.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .contracts import TERMINAL_TASK_STATES, CommandType, TaskState
from .db import bind_tenant
from .models import AuditEvent, Business, RunEvent, Task, WorkflowRun

RUN_CREATING = frozenset({
    CommandType.PLAN, CommandType.SIMULATE, CommandType.REPORT,
    CommandType.OPTIMIZE, CommandType.BUILD, CommandType.EXECUTE,
})
CONTROL = frozenset({CommandType.PAUSE, CommandType.RESUME, CommandType.STOP})

ALLOWED: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.RUNNING, TaskState.CANCELED, TaskState.FAILED}),
    TaskState.RUNNING: frozenset({
        TaskState.WAITING_APPROVAL, TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELED,
    }),
    TaskState.WAITING_APPROVAL: frozenset({
        TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELED,
    }),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELED: frozenset(),
}


class CommandError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def can_transition(current: str, new: str) -> bool:
    return TaskState(new) in ALLOWED[TaskState(current)]


def _now() -> datetime:
    return datetime.now(UTC)


def _event(db: Session, run: WorkflowRun, type_: str, **data) -> None:
    db.add(RunEvent(tenant_id=run.tenant_id, run_id=run.id, type=type_, data=data))


def transition(db: Session, run: WorkflowRun, new: TaskState) -> None:
    if not can_transition(run.status, new):
        raise CommandError(409, "invalid_transition", f"run is {run.status}, cannot become {new}")
    old, run.status = run.status, new.value
    if new == TaskState.RUNNING and run.started_at is None:
        run.started_at = _now()
    if new in TERMINAL_TASK_STATES:
        run.finished_at = _now()
        run.paused = False
    _event(db, run, "run.state_changed", **{"from": old, "to": new.value})


def add_task(db: Session, run: WorkflowRun, kind: str, title: str) -> Task:
    """Append a task. Refused once the run is finished.

    Spec vol. 06 acceptance: cancellation prevents new tasks.
    """
    if TaskState(run.status) in TERMINAL_TASK_STATES:
        raise CommandError(409, "run_finished", f"run is {run.status}; no new tasks allowed")
    seq = (db.scalar(select(func.max(Task.sequence)).where(Task.run_id == run.id)) or 0) + 1
    task = Task(tenant_id=run.tenant_id, run_id=run.id, sequence=seq, kind=kind, title=title)
    db.add(task)
    db.flush()
    _event(db, run, "task.created", task_id=str(task.id), sequence=seq, kind=kind)
    return task


def request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_run(
    db: Session, tenant_id: uuid.UUID, user_id: uuid.UUID, payload: dict
) -> tuple[WorkflowRun, bool]:
    """Returns (run, created). created=False means an idempotent replay."""
    ctype = CommandType(payload["type"])
    if ctype == CommandType.EXECUTE and payload.get("business_id") is None:
        # Vol. 04: EXECUTE only covers actions already inside granted scope. Scope is the
        # business's autonomy level, so EXECUTE must name a business. Every action inside
        # the run still goes through the policy engine (phase 07).
        raise CommandError(422, "execute_needs_business",
                           "EXECUTE must name a business_id (its autonomy level sets the scope)")
    if ctype not in RUN_CREATING:
        raise CommandError(422, "not_a_run_command", f"{ctype} controls an existing run")
    if not (payload.get("objective") or "").strip():
        raise CommandError(422, "objective_required", "objective is required")

    key = payload["idempotency_key"]
    digest = request_hash({k: v for k, v in payload.items() if k != "idempotency_key"})

    existing = _by_key(db, tenant_id, key)
    if existing is not None:
        return _replay(existing, digest), False

    business_id = payload.get("business_id")
    if business_id is not None and db.scalar(
        select(Business.id).where(Business.id == business_id, Business.tenant_id == tenant_id)
    ) is None:
        raise CommandError(422, "unknown_business", "business not found in this tenant")

    run = WorkflowRun(
        tenant_id=tenant_id, requested_by=user_id, business_id=business_id,
        command_type=ctype.value, objective=payload["objective"].strip(),
        budget_cents=payload.get("budget_cents"), deadline=payload.get("deadline"),
        constraints=payload.get("constraints") or [],
        success_criteria=payload.get("success_criteria") or [],
        idempotency_key=key, request_hash=digest,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent request with the same key won the race.
        db.rollback()
        bind_tenant(db, tenant_id)
        existing = _by_key(db, tenant_id, key)
        if existing is None:
            raise
        return _replay(existing, digest), False

    _event(db, run, "run.created", command_type=ctype.value)
    add_task(db, run, kind="plan", title=f"Plan: {run.objective[:280]}")
    db.add(AuditEvent(
        tenant_id=tenant_id, actor_user_id=user_id, action="command.accepted",
        entity_type="WorkflowRun", entity_id=run.id,
        data={"type": ctype.value, "idempotency_key": key},
    ))
    return run, True


def _by_key(db: Session, tenant_id: uuid.UUID, key: str) -> WorkflowRun | None:
    return db.scalar(select(WorkflowRun).where(
        WorkflowRun.tenant_id == tenant_id, WorkflowRun.idempotency_key == key
    ))


def _replay(run: WorkflowRun, digest: str) -> WorkflowRun:
    if run.request_hash != digest:
        raise CommandError(
            409, "idempotency_key_reused",
            "idempotency key was already used with a different request",
        )
    return run


def control_run(
    db: Session, run: WorkflowRun, ctype: CommandType, user_id: uuid.UUID
) -> WorkflowRun:
    """PAUSE / RESUME / STOP. Each one is idempotent: repeating it changes nothing."""
    finished = TaskState(run.status) in TERMINAL_TASK_STATES
    if ctype == CommandType.STOP:
        if run.status == TaskState.CANCELED:
            return run
        if finished:
            raise CommandError(409, "run_finished", f"run already {run.status}")
        transition(db, run, TaskState.CANCELED)
        for task in db.scalars(select(Task).where(Task.run_id == run.id)):
            if TaskState(task.status) not in TERMINAL_TASK_STATES:
                task.status = TaskState.CANCELED.value
        action = "run.canceled"
    elif ctype in (CommandType.PAUSE, CommandType.RESUME):
        if finished:
            raise CommandError(409, "run_finished", f"run already {run.status}")
        want = ctype == CommandType.PAUSE
        if run.paused == want:
            return run
        run.paused = want
        action = "run.paused" if want else "run.resumed"
        _event(db, run, action)
    else:
        raise CommandError(422, "not_a_control_command", f"{ctype} is not a control command")
    db.add(AuditEvent(
        tenant_id=run.tenant_id, actor_user_id=user_id, action=action,
        entity_type="WorkflowRun", entity_id=run.id, data={},
    ))
    return run
