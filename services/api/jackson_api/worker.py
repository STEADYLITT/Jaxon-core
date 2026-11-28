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

import hashlib
import json
import logging
import os
import signal
import socket
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from . import city, tools
from .agents import MAX_AGENTS_PER_RUN, MAX_DEPTH, QuotaExceeded, spec_dict
from .artifacts import LocalArtifactStore
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
from .memory import citation, recall, write_memory
from .models import (
    AgentSpec,
    AuditEvent,
    Business,
    Launch,
    ModelUsage,
    RunEvent,
    Task,
    Tenant,
    ToolCall,
    ToolGrant,
    WorkflowRun,
)
from .models_ai import ModelRequest, ModelResponse, ModelRouter
from .policy import Action, Category
from .runs import add_task, transition
from .tools.redact import redact
from .worker_errors import NonRetryableError

__all__ = ["ApprovalRequired", "NonRetryableError", "PolicyDenied", "TaskContext", "TaskResult",
           "Worker"]

log = logging.getLogger("jackson.worker")


@dataclass
class TaskResult:
    output: dict
    # (kind, title) or (kind, title, input)
    follow_ups: list[tuple] = field(default_factory=list)


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
    # Phase 23: a log line for the agent's live monitor in the City (agent.log event).
    log: Callable[[str], None] = lambda line: None
    # Phase 08: the only way handlers reach a model. It checks the tenant's monthly model
    # budget, routes to a provider (MOCK if none is configured) and meters usage.
    call_model: Callable[[str, "ModelRequest"], "ModelResponse"] = None  # type: ignore
    business: dict | None = None  # name/kind/autonomy of the run's business, if any
    # Phase 09: memory. Recall returns active, tenant-scope records with citations;
    # remember records provenance (this run/task) automatically.
    recall: Callable[..., list[dict]] = lambda query, limit=5: []
    remember: Callable[..., dict] = None  # type: ignore
    # Phase 10: the only way to use a tool (grant, schema, policy, budget, idempotency,
    # timeout, redaction, logging). Returns {"output": ..., "untrusted": True, ...}.
    use_tool: Callable[[str, dict], dict] = None  # type: ignore
    # Phase 11: `with ctx.browser() as b:` opens a watched, allowlisted sandbox browser.
    browser: Callable[[], object] = None  # type: ignore
    # Phase 12: agents. `input` carries a spawned task's payload; `agent_spec` is the spec
    # this task runs (dict) or None; spawn_agent delegates within quotas and narrower scope.
    input: dict = field(default_factory=dict)
    depth: int = 0
    agent_spec: dict | None = None
    spawn_agent: Callable[..., str] = None  # type: ignore
    # Worker-role DB session for built-in handlers that record their own rows (factory).
    db_session: Callable[[], Session] = None  # type: ignore
    # Phase 15: `ctx.shopify()` -> draft-only store connector (mock until credentials).
    shopify: Callable[[], object] = None  # type: ignore
    artifacts: object = None
    constraints: list = field(default_factory=list)
    success_criteria: list = field(default_factory=list)


Handler = Callable[[TaskContext], TaskResult]


def default_handlers() -> dict[str, Handler]:
    from .agent_handlers import agent_handler, eval_handler, factory_handler
    from .content import generate_handler as content_generate
    from .content import publish_handler as content_publish
    from .content import video_handler as content_video
    from .courses import build_handler as course_build
    from .crm import prepare_handler as outreach_prepare
    from .crm import send_handler as outreach_send
    from .dashboard import report_handler
    from .planner import plan_handler  # lazy: planner imports TaskResult from here
    from .research import research_handler
    from .routes_launches import propose_handler as launch_propose
    from .sites import build_handler as site_build
    from .sites import launch_handler as site_launch
    from .store_builder import publish_handler, store_build_handler
    from .voice import voice_handler as voice_generate
    from .whop_sync import sync_handler as whop_sync

    return {"plan": plan_handler, "agent": agent_handler, "agent_eval": eval_handler,
            "agent_factory": factory_handler, "opportunity_research": research_handler,
            "store_build": store_build_handler, "shopify_publish": publish_handler,
            "content_generate": content_generate, "content_publish": content_publish,
            "content_video": content_video,
            "site_build": site_build, "site_launch": site_launch,
            "outreach_prepare": outreach_prepare, "outreach_send": outreach_send,
            "daily_report": report_handler, "launch_propose": launch_propose,
            "course_build": course_build, "whop_sync": whop_sync,
            "voice_generate": voice_generate}


class ModelBudgetExhausted(NonRetryableError):
    pass


class ToolNotGranted(NonRetryableError):
    pass


def _summary(output) -> str:
    """A short, real summary of a task's output for the City (never invented)."""
    if not isinstance(output, dict):
        return str(output)[:200]
    for k in ("summary", "result", "title", "status"):
        if isinstance(output.get(k), str):
            return output[k][:200]
    return ", ".join(f"{k}={v}" for k, v in list(output.items())[:4]
                     if isinstance(v, (str, int, float, bool)))[:200]


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
        artifact_store: LocalArtifactStore | None = None,
        browser_poll_s: float = 0.5,
        isolate: bool = False,
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
        self.artifacts = artifact_store or LocalArtifactStore()
        self.browser_poll_s = browser_poll_s
        # Phase 23: run each task in a child process so the owner's Kill really stops it.
        self.isolate = isolate and hasattr(os, "fork")
        self._default_router = router is None
        # Held by the heartbeat thread while it uses the DB, and around fork(): the child
        # never inherits a connection pool or lock another thread was in the middle of.
        self._fork_lock = threading.Lock()
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
                .join(Tenant, Tenant.id == Task.tenant_id)
                .outerjoin(Launch, Launch.id == WorkflowRun.launch_id)
                .where(
                    Tenant.stopped_at.is_(None),  # global stop (also enforced by trigger)
                    # a paused/stopped launch holds its runs' work until the owner acts
                    (WorkflowRun.launch_id.is_(None)) | (Launch.status == "active"),
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
                if city.agent_for_task(db, task).status in ("paused", "killed"):
                    continue  # the owner paused or killed this agent: no new work
                if task.agent_spec_id is not None and not self._spec_has_capacity(db, task):
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
                city.on_task(db, task, "task.started", "working", attempt=task.attempts)
                db.commit()
                return task.id
            db.commit()
        return None

    def _spec_has_capacity(self, db: Session, task: Task) -> bool:
        limit = db.scalar(select(AgentSpec.max_concurrency).where(
            AgentSpec.id == task.agent_spec_id)) or 1
        running = db.scalar(select(func.count()).select_from(Task).where(
            Task.agent_spec_id == task.agent_spec_id, Task.status == TaskState.RUNNING.value))
        return running < limit

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
            spec = db.get(AgentSpec, task.agent_spec_id) if task.agent_spec_id else None
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
                log=lambda line: self._log(task_id, line),
                recall=lambda query, limit=5: self._recall(task_id, query, limit),
                remember=lambda **kw: self._remember(task_id, **kw),
                use_tool=lambda name, args: self._use_tool(task_id, name, args),
                browser=lambda: self._browser(task_id),
                business=({"name": biz.name, "kind": biz.kind,
                           "autonomy_level": biz.autonomy_level} if biz else None),
                constraints=list(run.constraints or []),
                success_criteria=list(run.success_criteria or []),
                input=dict(task.input or {}),
                depth=task.depth,
                agent_spec=(spec_dict(spec) | {"status": spec.status,
                                               "success_tests": spec.success_tests,
                                               "max_runtime_s": spec.max_runtime_s}
                            if spec else None),
                spawn_agent=lambda name, objective, context=None: self._spawn_agent(
                    task_id, name, objective, context),
                db_session=self.maker,
                shopify=lambda: self._shopify(task_id),
                artifacts=self.artifacts,
            )
        handler = self.handlers.get(ctx.kind)
        if self.isolate:
            self._run_isolated(task_id, ctx, handler)
        else:
            self._execute(task_id, ctx, handler)
        return True

    def _execute(self, task_id: uuid.UUID, ctx: "TaskContext", handler) -> None:
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

    # --- Isolation and the owner's Kill (phase 23) ---------------------------------------

    def _run_isolated(self, task_id: uuid.UUID, ctx: "TaskContext", handler,
                      poll_s: float = 0.25) -> None:
        """Run the task in a forked child. The parent watches for the owner's Kill; on Kill
        it SIGKILLs the child, then records it, then emits agent.killed (never before)."""
        import warnings

        with self._fork_lock, warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # guarded by _fork_lock
            pid = os.fork()
        if pid == 0:  # child: fresh DB connections and HTTP clients, then the real work
            code = 0
            try:
                engine = getattr(self.maker, "kw", {}).get("bind")
                if engine is not None:
                    engine.dispose(close=False)
                if self._default_router:
                    self.router = ModelRouter()
                self._stop.set()  # the child never serves the loop
                self._execute(task_id, ctx, handler)
            except BaseException:  # noqa: BLE001 - the parent records what is left
                code = 1
            finally:
                os._exit(code)
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            if self._kill_requested(task_id):
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                log.warning("task %s killed by the owner (pid %s stopped)", task_id, pid)
                self._record_killed(task_id, pid)
                return
            time.sleep(poll_s)
        if status != 0:
            self._record_failure(task_id, RuntimeError(
                f"task process exited unexpectedly (status {status})"))

    def _kill_requested(self, task_id: uuid.UUID) -> bool:
        with self.maker() as db:
            task = db.get(Task, task_id)
            if task is None:
                return False
            killed = city.agent_for_task(db, task).status == "killed"
            db.rollback()
            return killed

    def _record_killed(self, task_id: uuid.UUID, pid: int) -> None:
        with self.maker() as db:
            task = db.scalar(select(Task).where(Task.id == task_id).with_for_update())
            run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == task.run_id)
                            .with_for_update())
            if TaskState(task.status) not in TERMINAL_TASK_STATES:
                task.status = TaskState.CANCELED.value
                task.error = "killed by the owner"
                task.finished_at = func.now()
            task.lease_owner, task.lease_expires_at = None, None
            if TaskState(run.status) not in TERMINAL_TASK_STATES:
                transition(db, run, TaskState.CANCELED)
                for other in db.scalars(select(Task).where(Task.run_id == run.id,
                                                           Task.id != task.id)):
                    if TaskState(other.status) not in TERMINAL_TASK_STATES:
                        other.status = TaskState.CANCELED.value
            _event(db, task, "task.killed", pid=pid)
            a = city.agent_for_task(db, task)
            a.current_task_id = None
            city.emit(db, task.tenant_id, "agent.killed", agent_id=a.id, task_id=task.id,
                      run_id=task.run_id, payload={"title": task.title[:200],
                                                   "stopped": "process terminated"})
            db.add(AuditEvent(tenant_id=task.tenant_id, actor_user_id=None,
                              action="agent.killed", entity_type="Task", entity_id=task.id,
                              data={"pid": pid}))
            db.commit()

    def _log(self, task_id: uuid.UUID, line: str) -> None:
        """A handler's log line, shown live on the agent's monitor in the City."""
        with self.maker() as db:
            task = db.get(Task, task_id)
            if task is not None:
                city.on_task(db, task, "agent.log", line=str(line)[:1000])
                db.commit()

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
            if cents:
                a = city.agent_for_task(db, task)
                city.emit(db, task.tenant_id, "cost.recorded", agent_id=a.id, task_id=task.id,
                          run_id=task.run_id, payload={"usd": cents / 100, "source": "spend",
                                                       "description": description[:200]})
                city.cost_guard(db, a)
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
            agent_cap = None
            if task.agent_spec_id is not None:
                agent_cap = (db.scalar(select(AgentSpec.budget_cents_per_run).where(
                    AgentSpec.id == task.agent_spec_id)) or 0) * 10_000  # cents -> micros
                agent_spent = int(db.scalar(select(func.coalesce(
                    func.sum(ModelUsage.cost_usd_micros), 0)).where(
                    ModelUsage.task_id == task_id)) or 0)
            db.rollback()
        if agent_cap is not None and agent_spent >= agent_cap and agent_cap > 0:
            raise ModelBudgetExhausted(
                f"agent budget for this task used up ({agent_spent / 1e6:.4f} USD)")
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
            t = db.get(Task, task_id)
            a = city.on_task(db, t, "tool.called", tool="model", model=resp.model,
                             mode=resp.mode, profile=profile)
            a.model = resp.model
            if resp.cost_usd_micros:
                city.emit(db, tenant_id, "cost.recorded", agent_id=a.id, task_id=task_id,
                          run_id=run_id, payload={"usd": resp.cost_usd_micros / 1e6,
                                                  "source": "model", "model": resp.model})
                city.cost_guard(db, a)
            db.commit()
        return resp

    def _spawn_agent(self, task_id: uuid.UUID, name: str, objective: str,
                     context: dict | None) -> str:
        with self.maker() as db:
            parent, run = self._locked(db, task_id)
            run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == run.id)
                            .with_for_update())  # serialize spawns within a run
            depth = parent.depth + 1
            if depth > MAX_DEPTH:
                raise QuotaExceeded(f"delegation depth {depth} exceeds the limit of {MAX_DEPTH}")
            n_agents = db.scalar(select(func.count()).select_from(Task).where(
                Task.run_id == run.id, Task.agent_spec_id.is_not(None))) or 0
            if n_agents >= MAX_AGENTS_PER_RUN:
                raise QuotaExceeded(f"run already has {n_agents} agent tasks "
                                    f"(limit {MAX_AGENTS_PER_RUN})")
            spec = db.scalar(select(AgentSpec).where(
                AgentSpec.tenant_id == parent.tenant_id, AgentSpec.name == name,
                AgentSpec.status == "active"))
            if spec is None:
                raise NonRetryableError(f"no active agent named '{name}'")
            scope = list(spec.allowed_tools)
            if parent.tool_scope is not None:  # never broader than the parent
                scope = [t for t in scope if t in parent.tool_scope]
            child = add_task(db, run, kind="agent", title=f"{name}: {objective[:250]}",
                             parent_task_id=parent.id, depth=depth, agent_spec_id=spec.id,
                             tool_scope=scope, input={"objective": objective,
                                                      "context": context or {}})
            _event(db, parent, "agent.spawned", child_task_id=str(child.id), agent=name,
                   version=spec.version, depth=depth, tool_scope=scope)
            db.commit()
            return str(child.id)

    def _recall(self, task_id: uuid.UUID, query: str, limit: int) -> list[dict]:
        with self.maker() as db:
            task, run = self._locked(db, task_id)
            rows = recall(db, task.tenant_id, query, business_id=run.business_id, limit=limit)
            out = [{"memory_id": str(r.id), "kind": r.kind, "epistemic": r.epistemic,
                    "content": r.content, "citation": citation(r)} for r in rows]
            db.rollback()
            return out

    def _remember(self, task_id: uuid.UUID, *, kind: str, epistemic: str, content: str,
                  key: str | None = None, impact: str = "low",
                  confidence: float = 0.7) -> dict:
        with self.maker() as db:
            task, run = self._locked(db, task_id)
            rec = write_memory(
                db, tenant_id=task.tenant_id, business_id=run.business_id, kind=kind,
                epistemic=epistemic, content=content, key=key, impact=impact,
                confidence=confidence, source_type="run",
                source_ref=f"{task.run_id}:{task.sequence}",
                source_detail={"run_id": str(task.run_id), "task_id": str(task.id)},
                agent=self.worker_id)
            _event(db, task, "memory.written", memory_id=str(rec.id), status=rec.status)
            db.commit()
            return {"memory_id": str(rec.id), "status": rec.status}

    @staticmethod
    def _grant(db: Session, task: Task, run: WorkflowRun, name: str) -> ToolGrant | None:
        return db.scalar(select(ToolGrant).where(
            ToolGrant.tenant_id == task.tenant_id, ToolGrant.tool_name == name,
            ToolGrant.status == "active",
            or_(ToolGrant.business_id == run.business_id, ToolGrant.business_id.is_(None)))
            .order_by(ToolGrant.business_id.is_(None)))  # business-specific grant first

    def _shopify(self, task_id: uuid.UUID):
        from .shop import ShopHandle

        with self.maker() as db:
            task, run = self._locked(db, task_id)
            if task.tool_scope is not None and "shopify.drafts" not in task.tool_scope:
                raise ToolNotGranted("tool 'shopify.drafts' is outside this agent's tool scope")
            grant = self._grant(db, task, run, "shopify.drafts")
            if grant is None:
                _event(db, task, "tool.denied", tool="shopify.drafts", reason="no active grant")
                db.commit()
                raise ToolNotGranted("tool 'shopify.drafts' is not granted for this business")
            config = dict(grant.config or {})
            db.rollback()
        return ShopHandle(self, task_id, config)

    def _browser(self, task_id: uuid.UUID):
        from .browser import BrowserHandle  # lazy: Playwright loads only when used

        with self.maker() as db:
            task, run = self._locked(db, task_id)
            grant = self._grant(db, task, run, "browser.session")
            if grant is None:
                _event(db, task, "tool.denied", tool="browser.session", reason="no active grant")
                db.commit()
                raise ToolNotGranted("tool 'browser.session' is not granted for this business")
            domains = list((grant.config or {}).get("allowed_domains", []))
            db.rollback()
        if not domains:
            raise NonRetryableError("browser grant has no allowed_domains")
        return BrowserHandle(self, task_id, domains, poll_s=self.browser_poll_s)

    def _use_tool(self, task_id: uuid.UUID, name: str, args: dict) -> dict:
        spec = tools.get(name)
        if spec is None:
            raise NonRetryableError(f"unknown tool '{name}'")
        try:
            parsed = spec.input_model.model_validate(args)
        except ValidationError as e:
            raise NonRetryableError(f"invalid input for {name}: {e.errors()[:3]}") from None
        digest = hashlib.sha256(json.dumps(parsed.model_dump(mode="json"), sort_keys=True)
                                .encode()).hexdigest()[:24]
        with self.maker() as db:
            task, run = self._locked(db, task_id)
            key = f"{task.run_id}:{task.sequence}:{name}:{digest}"
            done = db.scalar(select(ToolCall).where(
                ToolCall.tenant_id == task.tenant_id, ToolCall.idempotency_key == key,
                ToolCall.status == "succeeded"))
            if done is not None:  # retried task: never repeat the side effect
                db.rollback()
                return {"tool": name, "mode": done.mode, "untrusted": True, "cached": True,
                        "output": done.output}
            if task.tool_scope is not None and name not in task.tool_scope:
                _event(db, task, "tool.denied", tool=name, reason="outside this agent's scope")
                db.commit()
                raise ToolNotGranted(f"tool '{name}' is outside this agent's tool scope")
            grant = self._grant(db, task, run, name)
            if grant is None:
                _event(db, task, "tool.denied", tool=name, reason="no active grant")
                db.commit()
                raise ToolNotGranted(f"tool '{name}' is not granted for this business")
            config = dict(grant.config or {})
            try:
                if spec.cost_cents > 0:
                    reserve(db, task, spec.cost_cents, f"tool {name}", f"{key}:reserve")
                if spec.category != Category.SPEND:
                    enforce(db, task, run, Action(spec.category, f"tool {name}: {digest}",
                                                  spec.cost_cents))
            finally:
                db.commit()
            tenant_id, run_id = task.tenant_id, task.run_id
        started = time.monotonic()
        error, output = None, None
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(spec.run, parsed, tools.ToolEnv(config, key))
                output = fut.result(timeout=spec.timeout_s)
        except FutureTimeout:
            error = NonRetryableError(f"tool {name} timed out after {spec.timeout_s}s")
        except Exception as exc:  # noqa: BLE001 - recorded below, then re-raised
            error = exc
        ms = int((time.monotonic() - started) * 1000)
        if output is not None:
            text_out = json.dumps(output, default=str)
            if len(text_out) > spec.max_output_chars:
                output = {"truncated": True, "text": text_out[:spec.max_output_chars]}
        safe_in, safe_out = redact(parsed.model_dump(mode="json")), redact(output)
        with self.maker() as db:
            db.add(ToolCall(
                tenant_id=tenant_id, run_id=run_id, task_id=task_id, tool_name=name,
                mode=spec.mode, input=safe_in, output=safe_out,
                status="failed" if error else "succeeded",
                error=redact(f"{type(error).__name__}: {error}")[:2000] if error else None,
                duration_ms=ms,
                idempotency_key=key if not error else f"{key}:failed:{uuid.uuid4().hex[:8]}"))
            db.add(RunEvent(tenant_id=tenant_id, run_id=run_id, type="tool.called", data={
                "task_id": str(task_id), "tool": name, "mode": spec.mode,
                "status": "failed" if error else "succeeded", "duration_ms": ms}))
            city.on_task(db, db.get(Task, task_id), "tool.called", tool=name, mode=spec.mode,
                         outcome="failed" if error else "succeeded", duration_ms=ms)
            db.commit()
        if error:
            raise error
        return {"tool": name, "mode": spec.mode, "untrusted": True, "cached": False,
                "output": safe_out}

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
            city.on_task(db, task, "approval.requested", "waiting", approval_id=str(ap.id),
                         question=exc.action.description[:300],
                         amount_cents=exc.action.amount_cents, reason=exc.reason[:300])
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
                city.on_task(db, task, "agent.log", "idle", line="run canceled; result discarded")
                db.commit()
                return
            task.status = TaskState.SUCCEEDED.value
            task.output = result.output
            task.finished_at = func.now()
            task.lease_owner = None
            task.lease_expires_at = None
            _event(db, task, "task.succeeded", attempt=task.attempts)
            city.on_task(db, task, "task.completed", "done", attempt=task.attempts,
                         result_summary=_summary(result.output))
            route = (result.output or {}).get("route") if isinstance(result.output, dict) else None
            if route and route.get("business_id") and run.business_id is None:
                run.business_id = uuid.UUID(route["business_id"])  # the line's business
            created = []
            for kind, title, *more in result.follow_ups:
                extra = dict(more[1]) if len(more) > 1 else {}
                if "agent_spec_id" in extra:
                    extra["agent_spec_id"] = uuid.UUID(str(extra["agent_spec_id"]))
                created.append(add_task(db, run, kind=kind, title=title,
                                        **({"input": more[0]} if more else {}), **extra))
            if route:  # Jaxon's routing decision, with its reason, next to the task it made
                to = city.agent_for_task(db, created[0]) if created else None
                dest = city.address(to) if to else ("jaxon", "jaxon")
                if to is None and route.get("line"):  # Jaxon keeps it, for this line
                    d = city.ensure_departments(db, task.tenant_id).get(route["line"])
                    dest = ("department", str(d.id)) if d else dest
                city.emit(db, task.tenant_id, "command.routed", from_=("jaxon", "jaxon"),
                          to=dest, run_id=run.id,
                          task_id=created[0].id if created else None,
                          agent_id=to.id if to else None,
                          payload={"command": run.objective[:300], "reason": route["reason"],
                                   "via": route["via"], "agent": to.name if to else "Jaxon",
                                   "kind": route.get("kind"), "line": route.get("line"),
                                   "missing": route.get("missing") or [],
                                   "refused": bool(route.get("refused"))})
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
            city.on_task(db, task, "agent.log", "idle",
                         line=f"attempt {task.attempts} failed, retrying in {delay:.0f}s: "
                              f"{message[:300]}")
            return
        # Dead letter: keep the task and its error for inspection; fail the run.
        task.status = TaskState.FAILED.value
        task.finished_at = func.now()
        _event(db, task, "task.dead_lettered", attempts=task.attempts, error=message)
        failed_agent = city.on_task(db, task, "task.failed", "failed", error=message[:500])
        city.failure_siren(db, failed_agent)
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

    def sweep_launches(self) -> list:
        """Auto-pause / kill / expire launches whose rules fire (phase 21)."""
        from .launches import sweep

        with self.maker() as db:
            changed = sweep(db)
            db.commit()
        for lid, status in changed:
            log.warning("launch %s -> %s", lid, status)
        return changed

    def city_heartbeat(self) -> int:
        """Tell the City this worker is alive (every city.HEARTBEAT_S)."""
        with self.maker() as db:
            for tid in db.scalars(select(Tenant.id)):
                city.ensure_registry(db, tid)
            running = db.scalars(select(Task).where(
                Task.status == TaskState.RUNNING.value,
                Task.lease_owner == self.worker_id)).all()
            n = city.heartbeat(db, list(running))
            db.commit()
        return n

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._fork_lock:
                    self.city_heartbeat()
            except Exception:  # noqa: BLE001 - a missed beat just shows as offline
                log.exception("city heartbeat error")
            self._stop.wait(city.HEARTBEAT_S)

    def serve(self, idle_sleep: float = 1.0) -> None:
        log.info("worker %s started", self.worker_id)
        threading.Thread(target=self._heartbeat_loop, name="city-heartbeat",
                         daemon=True).start()
        every = float(os.environ.get("LAUNCH_SWEEP_SECONDS", "60"))
        last_sweep = 0.0
        while not self._stop.is_set():
            if time.monotonic() - last_sweep >= every:
                last_sweep = time.monotonic()
                try:
                    self.sweep_launches()
                except Exception:  # noqa: BLE001 - never let the sweep stop the worker
                    log.exception("launch sweep error")
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
                    tenant_concurrency=int(os.environ.get("WORKER_TENANT_CONCURRENCY", "4")),
                    isolate=os.environ.get("WORKER_ISOLATE", "1") != "0")
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.serve()


if __name__ == "__main__":
    main()
