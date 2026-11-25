"""Durable task worker on a Postgres-backed queue (phase 06, ADR-016).

How a task moves:
  queued --claim (lease)--> running --handler ok--> succeeded
                                    --handler error--> queued again after backoff,
                                                       or failed ("dead-lettered")
                                    --lease expires (worker crashed)--> queued again
  A canceled run's tasks are never started, and results that come back after a
  cancel are thrown away. Paused runs are skipped.

Guarantees:
- Claims use SELECT ... FOR UPDATE SKIP LOCKED, so two workers never get the same task.
- Every result is written only if this worker still holds the lease.
- Handlers get `idempotency_key` = "<run_id>:<sequence>". Connectors (phase 10+)
  must pass it on to external APIs so a retried task never repeats a side effect.
- A per-tenant concurrency quota stops one tenant from taking every worker.

Run:  WORKER_DATABASE_URL=... python -m jackson_api.worker
"""

import logging
import os
import signal
import socket
import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .contracts import TERMINAL_TASK_STATES, TaskState
from .db import make_sessionmaker
from .governance import (
    ApprovalRequired,
    PolicyDenied,
    audit,
    enforce,
    request_approval,
    reserve,
    settle,
)
from .models import AuditEvent, Business, ModelUsage, RunEvent, Task, WorkflowRun
from .models_ai import ModelRequest, ModelResponse, ModelRouter
from .policy import Action
from .runs import add_task, transition
from .worker_errors import NonRetryableError

__all__ = ["ApprovalRequired", "NonRetryableError", "PolicyDenied", "TaskContext", "TaskResult",
           "Worker"]

log = logging.getLogger("jackson.worker")


@dataclass
class TaskResult:
    output: dict
    follow_ups: list[tuple[str, str]] = field(default_factory=list)  # (kind, title)


@dataclass
class TaskContext:
    tenant_id: uuid.UUID
    run_id: uuid.UUID
    task_id: uuid.UUID
    kind: str
    title: str
    attempt: int
    idempotency_key: str
    objective: str
    command_type: str
    business_id: uuid.UUID | None
    budget_cents: int | None
    heartbeat: Callable[[], bool]  # extends the lease; False => stop work now
    should_stop: Callable[[], bool]  # True if the run was canceled/paused or lease lost
    # Phase 07: every side effect must pass policy first. These raise ApprovalRequired
    # (the task then waits for a human) or PolicyDenied (the task fails).
    authorize: Callable[[Action], None] = lambda action: None
    reserve_budget: Callable[[int, str], uuid.UUID] = lambda cents, desc: uuid.uuid4()
    settle_spend: Callable[[uuid.UUID, int, str], None] = lambda rid, cents, desc: None
    # Phase 08: the only way handlers reach a model. It checks the tenant's monthly model
    # budget, routes to a provider (MOCK if none is configured) and meters usage.
    call_model: Callable[[str, "ModelRequest"], "ModelResponse"] = None  # type: ignore
    business: dict | None = None  # name/kind/autonomy of the run's business, if any
    constraints: list = field(default_factory=list)
    success_criteria: list = field(default_factory=list)


Handler = Callable[[TaskContext], TaskResult]


def default_handlers() -> dict[str, Handler]:
    from .planner import plan_handler  # lazy: planner imports TaskResult from here

    return {"plan": plan_handler}


class ModelBudgetExhausted(NonRetryableError):
    pass


def _event(db: Session, task: Task, type_: str, **data) -> None:
    db.add(RunEvent(tenant_id=task.tenant_id, run_id=task.run_id, type=type_,
                    data={"task_id": str(task.id), "sequence": task.sequence, **data}))


class Worker:
    def __init__(
        self,
        maker: sessionmaker[Session],
        handlers: dict[str, Handler] | None = None,
        worker_id: str | None = None,
        lease_seconds: float = 60,
        retry_base_seconds: float = 5,
        retry_max_seconds: float = 300,
        tenant_concurrency: int = 4,
        router: ModelRouter | None = None,
        model_monthly_budget_usd: float | None = None,
    ):
        self.maker = maker
        self.handlers = handlers if handlers is not None else default_handlers()
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.lease = timedelta(seconds=lease_seconds)
        self.retry_base = retry_base_seconds
        self.retry_max = retry_max_seconds
        self.tenant_concurrency = tenant_concurrency
        self.router = router if router is not None else ModelRouter()
        budget = model_monthly_budget_usd if model_monthly_budget_usd is not None else float(
            os.environ.get("MODEL_MONTHLY_BUDGET_USD", "20"))
        self.model_budget_micros = int(budget * 1_000_000)
        self._stop = threading.Event()

    # --- Queue mechanics --------------------------------------------------------

    def reap_expired_leases(self) -> int:
        """Requeue tasks whose worker died (lease expired). Returns how many."""
        n = 0
        with self.maker() as db:
            expired = db.scalars(
                select(Task).where(Task.status == TaskState.RUNNING.value,
                                   Task.lease_expires_at < func.now())
                .with_for_update(skip_locked=True)
            ).all()
            for task in expired:
                n += 1
                _event(db, task, "task.lease_expired", lease_owner=task.lease_owner)
                self._fail_or_retry(db, task, "lease expired (worker stopped responding)")
            db.commit()
        return n

    def claim(self) -> uuid.UUID | None:
        with self.maker() as db:
            candidates = db.scalars(
                select(Task)
                .join(WorkflowRun, WorkflowRun.id == Task.run_id)
                .where(
                    Task.status == TaskState.QUEUED.value,
                    (Task.next_attempt_at.is_(None)) | (Task.next_attempt_at <= func.now()),
                    WorkflowRun.paused.is_(False),
                    WorkflowRun.status.in_([TaskState.QUEUED.value, TaskState.RUNNING.value]),
                )
                .order_by(Task.created_at, Task.sequence)
                .with_for_update(of=Task, skip_locked=True)
                .limit(20)
            ).all()
            for task in candidates:
                if not self._tenant_has_capacity(db, task.tenant_id):
                    continue
                run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == task.run_id)
                                .with_for_update())
                if run.paused or TaskState(run.status) in TERMINAL_TASK_STATES:
                    continue
                if run.status == TaskState.QUEUED.value:
                    transition(db, run, TaskState.RUNNING)
                task.status = TaskState.RUNNING.value
                task.attempts += 1
                task.lease_owner = self.worker_id
                task.lease_expires_at = func.now() + self.lease
                task.started_at = task.started_at or func.now()
                task.next_attempt_at = None
                _event(db, task, "task.started", attempt=task.attempts, worker=self.worker_id)
                db.commit()
                return task.id
            db.commit()
        return None

    def _tenant_has_capacity(self, db: Session, tenant_id: uuid.UUID) -> bool:
        running = db.scalar(select(func.count()).select_from(Task).where(
            Task.tenant_id == tenant_id, Task.status == TaskState.RUNNING.value))
        return running < self.tenant_concurrency

    def _owns(self, db: Session, task_id: uuid.UUID) -> tuple[Task | None, WorkflowRun | None]:
        task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None or task.status != TaskState.RUNNING.value \
                or task.lease_owner != self.worker_id:
            return None, None
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == task.run_id)
                        .with_for_update())
        return task, run

    def _heartbeat(self, task_id: uuid.UUID) -> bool:
        with self.maker() as db:
            task, run = self._owns(db, task_id)
            if task is None or run.status == TaskState.CANCELED.value:
                db.rollback()
                return False
            task.lease_expires_at = func.now() + self.lease
            db.commit()
            return not run.paused

    def _should_stop(self, task_id: uuid.UUID) -> bool:
        with self.maker() as db:
            task = db.get(Task, task_id)
            run = db.get(WorkflowRun, task.run_id) if task else None
            return (task is None or task.lease_owner != self.worker_id
                    or run is None or run.status == TaskState.CANCELED.value or run.paused)

    # --- Execution ---------------------------------------------------------------

    def run_once(self) -> bool:
        """Reap, claim one task, execute it, record the outcome. Returns True if work ran."""
        self.reap_expired_leases()
        task_id = self.claim()
        if task_id is None:
            return False
        with self.maker() as db:
            task = db.get(Task, task_id)
            run = db.get(WorkflowRun, task.run_id)
            biz = db.get(Business, run.business_id) if run.business_id else None
            ctx = TaskContext(
                tenant_id=task.tenant_id, run_id=task.run_id, task_id=task.id,
                kind=task.kind, title=task.title, attempt=task.attempts,
                idempotency_key=f"{task.run_id}:{task.sequence}",
                objective=run.objective, command_type=run.command_type,
                business_id=run.business_id, budget_cents=run.budget_cents,
                heartbeat=lambda: self._heartbeat(task_id),
                should_stop=lambda: self._should_stop(task_id),
                authorize=lambda action: self._authorize(task_id, action),
                reserve_budget=lambda cents, desc: self._reserve(task_id, cents, desc),
                settle_spend=lambda rid, cents, desc: self._settle(task_id, rid, cents, desc),
                call_model=lambda profile, req: self._call_model(task_id, profile, req),
                business=({"name": biz.name, "kind": biz.kind,
                           "autonomy_level": biz.autonomy_level} if biz else None),
                constraints=list(run.constraints or []),
                success_criteria=list(run.success_criteria or []),
            )
        handler = self.handlers.get(ctx.kind)
        try:
            if handler is None:
                raise NonRetryableError(f"no handler registered for task kind '{ctx.kind}'")
            result = handler(ctx)
        except ApprovalRequired as exc:
            self._record_waiting(task_id, exc)
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, none escape
            self._record_failure(task_id, exc)
        else:
            self._record_success(task_id, result)
        return True

    # --- Policy hooks (phase 07) ------------------------------------------------------

    def _locked(self, db: Session, task_id: uuid.UUID) -> tuple[Task, WorkflowRun]:
        task, run = self._owns(db, task_id)
        if task is None:
            raise NonRetryableError("lease lost; refusing to act")
        if run.status == TaskState.CANCELED.value:
            raise NonRetryableError("run was canceled; refusing to act")
        return task, run

    def _authorize(self, task_id: uuid.UUID, action: Action) -> None:
        with self.maker() as db:
            task, run = self._locked(db, task_id)
            try:
                enforce(db, task, run, action)
            finally:
                db.commit()  # keep the policy.checked event either way

    def _reserve(self, task_id: uuid.UUID, cents: int, description: str) -> uuid.UUID:
        with self.maker() as db:
            task, _ = self._locked(db, task_id)
            key = f"{task.run_id}:{task.sequence}:reserve:{cents}:{description}"[:200]
            try:
                entry = reserve(db, task, cents, description, key)
            finally:
                db.commit()
            return entry.id

    def _settle(self, task_id: uuid.UUID, reservation_id: uuid.UUID, cents: int,
                description: str) -> None:
        with self.maker() as db:
            task, _ = self._locked(db, task_id)
            settle(db, reservation_id, cents, description,
                   f"{task.run_id}:{task.sequence}:settle:{reservation_id}")
            db.commit()

    def _model_spend_this_month(self, db: Session, tenant_id: uuid.UUID) -> int:
        # SUM(bigint) comes back as NUMERIC (Decimal); keep integer micro-dollars.
        return int(db.scalar(
            select(func.coalesce(func.sum(ModelUsage.cost_usd_micros), 0)).where(
                ModelUsage.tenant_id == tenant_id,
                ModelUsage.created_at >= func.date_trunc("month", func.now()))) or 0)

    def _call_model(self, task_id: uuid.UUID, profile: str, req: ModelRequest) -> ModelResponse:
        with self.maker() as db:
            task, _ = self._locked(db, task_id)
            tenant_id, run_id = task.tenant_id, task.run_id
            spent = self._model_spend_this_month(db, tenant_id)
            db.rollback()
        if spent >= self.model_budget_micros:
            raise ModelBudgetExhausted(
                f"monthly model budget used up ({spent / 1e6:.2f} of "
                f"{self.model_budget_micros / 1e6:.2f} USD)")
        route = self.router.select(profile)
        resp = route.provider.complete(replace(req, effort=route.effort), route.model)
        resp.notes = [route.reason, *resp.notes]
        with self.maker() as db:
            db.add(ModelUsage(tenant_id=tenant_id, run_id=run_id, task_id=task_id,
                              provider=resp.provider, model=resp.model, mode=resp.mode,
                              profile=profile, input_tokens=resp.input_tokens,
                              output_tokens=resp.output_tokens,
                              cost_usd_micros=resp.cost_usd_micros, request_id=resp.request_id))
            db.add(RunEvent(tenant_id=tenant_id, run_id=run_id, type="model.called", data={
                "task_id": str(task_id), "provider": resp.provider, "model": resp.model,
                "mode": resp.mode, "profile": profile, "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens, "cost_usd_micros": resp.cost_usd_micros,
                "route": route.reason}))
            db.commit()
        return resp

    def _record_waiting(self, task_id: uuid.UUID, exc: ApprovalRequired) -> None:
        with self.maker() as db:
            task, run = self._owns(db, task_id)
            if task is None:
                self._discard(task_id, "lease lost before approval request was saved")
                return
            if run.status == TaskState.CANCELED.value:
                task.status = TaskState.CANCELED.value
                task.lease_owner = None
                db.commit()
                return
            ap = request_approval(db, task, exc.action, exc.reason)
            task.status = TaskState.WAITING_APPROVAL.value
            task.attempts = max(task.attempts - 1, 0)  # waiting is not a failed attempt
            task.lease_owner = None
            task.lease_expires_at = None
            if run.status == TaskState.RUNNING.value:
                transition(db, run, TaskState.WAITING_APPROVAL)
            _event(db, task, "approval.requested", approval_id=str(ap.id),
                   category=exc.action.category, amount_cents=exc.action.amount_cents,
                   reason=exc.reason)
            audit(db, task.tenant_id, None, "approval.requested", "Approval", ap.id,
                  category=exc.action.category, reason=exc.reason)
            db.commit()

    def _record_success(self, task_id: uuid.UUID, result: TaskResult) -> None:
        with self.maker() as db:
            task, run = self._owns(db, task_id)
            if task is None:
                self._discard(task_id, "lease lost before result was saved")
                return
            if run.status == TaskState.CANCELED.value:
                task.status = TaskState.CANCELED.value
                task.finished_at = func.now()
                task.lease_owner = None
                _event(db, task, "task.result_discarded", reason="run was canceled")
                db.commit()
                return
            task.status = TaskState.SUCCEEDED.value
            task.output = result.output
            task.finished_at = func.now()
            task.lease_owner = None
            task.lease_expires_at = None
            _event(db, task, "task.succeeded", attempt=task.attempts)
            for kind, title in result.follow_ups:
                add_task(db, run, kind=kind, title=title)
            db.flush()
            remaining = db.scalar(select(func.count()).select_from(Task).where(
                Task.run_id == run.id,
                Task.status.not_in([s.value for s in TERMINAL_TASK_STATES])))
            if remaining == 0:
                transition(db, run, TaskState.SUCCEEDED)
            db.commit()

    def _record_failure(self, task_id: uuid.UUID, exc: Exception) -> None:
        with self.maker() as db:
            task, run = self._owns(db, task_id)
            if task is None:
                self._discard(task_id, "lease lost before failure was saved")
                return
            if run.status == TaskState.CANCELED.value:
                task.status = TaskState.CANCELED.value
                task.finished_at = func.now()
                task.lease_owner = None
                db.commit()
                return
            message = f"{type(exc).__name__}: {exc}"[:2000]
            log.warning("task %s failed (attempt %s): %s", task_id, task.attempts, message)
            permanent = isinstance(exc, NonRetryableError)
            self._fail_or_retry(db, task, message, permanent=permanent,
                                detail="".join(traceback.format_exception_only(exc))[:500])
            db.commit()

    def _fail_or_retry(self, db: Session, task: Task, message: str, *,
                       permanent: bool = False, detail: str | None = None) -> None:
        task.error = message
        task.lease_owner = None
        task.lease_expires_at = None
        if not permanent and task.attempts < task.max_attempts:
            delay = min(self.retry_base * (2 ** max(task.attempts - 1, 0)), self.retry_max)
            task.status = TaskState.QUEUED.value
            task.next_attempt_at = func.now() + timedelta(seconds=delay)
            _event(db, task, "task.retry_scheduled", attempt=task.attempts,
                   retry_in_seconds=delay, error=message)
            return
        # Dead letter: keep the task and its error for inspection; fail the run.
        task.status = TaskState.FAILED.value
        task.finished_at = func.now()
        _event(db, task, "task.dead_lettered", attempts=task.attempts, error=message)
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == task.run_id)
                        .with_for_update())
        if TaskState(run.status) not in TERMINAL_TASK_STATES:
            transition(db, run, TaskState.FAILED)
            for other in db.scalars(select(Task).where(Task.run_id == run.id,
                                                       Task.id != task.id)):
                if TaskState(other.status) not in TERMINAL_TASK_STATES:
                    other.status = TaskState.CANCELED.value
        db.add(AuditEvent(tenant_id=task.tenant_id, actor_user_id=None,
                          action="task.dead_lettered", entity_type="Task", entity_id=task.id,
                          data={"error": message, "detail": detail}))

    def _discard(self, task_id: uuid.UUID, reason: str) -> None:
        """Record (never silently drop) a result this worker is no longer allowed to save."""
        log.warning("task %s result discarded: %s", task_id, reason)
        with self.maker() as db:
            task = db.get(Task, task_id)
            if task is not None:
                _event(db, task, "task.result_discarded", reason=reason, worker=self.worker_id,
                       task_status=task.status)
                db.commit()

    # --- Process loop -------------------------------------------------------------

    def stop(self, *_args) -> None:
        self._stop.set()

    def serve(self, idle_sleep: float = 1.0) -> None:
        log.info("worker %s started", self.worker_id)
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:  # noqa: BLE001 - keep serving; the DB holds the truth
                log.exception("worker loop error")
                worked = False
            if not worked:
                self._stop.wait(idle_sleep)
        log.info("worker %s stopped", self.worker_id)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    url = os.environ.get("WORKER_DATABASE_URL")
    if not url:
        raise SystemExit("WORKER_DATABASE_URL is not set (connect as role jackson_worker)")
    worker = Worker(make_sessionmaker(url),
                    tenant_concurrency=int(os.environ.get("WORKER_TENANT_CONCURRENCY", "4")))
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.serve()


if __name__ == "__main__":
    main()
