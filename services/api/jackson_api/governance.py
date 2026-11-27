"""Applies policy to real tasks, plus the budget ledger and approvals (phase 07).

- `authorize()` loads the business autonomy level, remaining budget and any approval
  from the DATABASE, then calls the pure `policy.evaluate()`.
- `reserve()` locks the run row before checking budget, so concurrent reservations
  can't overspend ("budget reservations prevent concurrent overspend", vol. 04).
- Ledger entries are append-only (the DB grants allow no UPDATE/DELETE).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .contracts import TERMINAL_TASK_STATES, TaskState
from .models import Approval, AuditEvent, Business, LedgerEntry, RunEvent, Task, WorkflowRun
from .policy import Action, Category, Decision, Outcome, evaluate
from .runs import CommandError, transition
from .worker_errors import NonRetryableError


class ApprovalRequired(Exception):  # noqa: N818 - control flow, not an error
    def __init__(self, action: Action, reason: str):
        super().__init__(reason)
        self.action, self.reason = action, reason


class PolicyDenied(NonRetryableError):
    pass


class LedgerError(NonRetryableError):
    pass


def budget_summary(db: Session, run: WorkflowRun) -> dict:
    sums = dict(db.execute(
        select(LedgerEntry.kind, func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
        .where(LedgerEntry.run_id == run.id).group_by(LedgerEntry.kind)).all())
    reserved, committed, released = (int(sums.get(k, 0)) for k in ("reserve", "commit", "release"))
    exposure = reserved - released  # money committed or still held
    budget = run.budget_cents or 0
    return {
        "budget_cents": budget,
        "committed_cents": committed,
        "open_reservations_cents": exposure - committed,
        "exposure_cents": exposure,
        "remaining_cents": max(budget - exposure, 0),
    }


def _autonomy(db: Session, run: WorkflowRun) -> int:
    if run.business_id is None:
        return 1  # no business => drafts/simulations only
    level = db.scalar(select(Business.autonomy_level).where(Business.id == run.business_id))
    return 1 if level is None else level


def _approved(db: Session, task: Task, action: Action) -> bool:
    return db.scalar(select(Approval.id).where(
        Approval.task_id == task.id, Approval.fingerprint == action.fingerprint(task.id),
        Approval.status == "approved")) is not None


def decide(db: Session, task: Task, run: WorkflowRun, action: Action) -> Decision:
    d = evaluate(
        action,
        autonomy_level=_autonomy(db, run),
        budget_remaining_cents=budget_summary(db, run)["remaining_cents"],
        approved=_approved(db, task, action),
    )
    if run.launch_id is None or d.outcome == Outcome.DENY:
        return d
    # Phase 21: inside an owner's Launch approval, in-scope actions run without asking
    # (level 3 by the owner's decision). Out of scope still goes to the inbox, and spend
    # always has to fit the launch's caps, whatever the business autonomy level.
    from .launches import check_health, covers
    from .models import Launch

    launch = db.get(Launch, run.launch_id)
    if launch is not None:
        check_health(db, launch)  # anomalies / kill rules stop it before it acts
    ok, why = covers(db, run.launch_id, run.business_id, action,
                     lock=action.category == Category.SPEND)
    if ok:
        return Decision(Outcome.ALLOW, why)
    if d.outcome == Outcome.ALLOW and action.category == Category.SPEND and \
            not _approved(db, task, action):
        return Decision(Outcome.REQUIRE_APPROVAL, f"outside the launch: {why}")
    if d.outcome == Outcome.REQUIRE_APPROVAL:
        return Decision(Outcome.REQUIRE_APPROVAL, f"{d.reason}; outside the launch: {why}")
    return d


def enforce(db: Session, task: Task, run: WorkflowRun, action: Action) -> None:
    d = decide(db, task, run, action)
    db.add(RunEvent(tenant_id=task.tenant_id, run_id=run.id, type="policy.checked",
                    data={"task_id": str(task.id), "category": action.category,
                          "amount_cents": action.amount_cents, "outcome": d.outcome,
                          "reason": d.reason}))
    if d.outcome == Outcome.DENY:
        raise PolicyDenied(d.reason)
    if d.outcome == Outcome.REQUIRE_APPROVAL:
        raise ApprovalRequired(action, d.reason)


def request_approval(db: Session, task: Task, action: Action, reason: str) -> Approval:
    fp = action.fingerprint(task.id)
    existing = db.scalar(select(Approval).where(Approval.task_id == task.id,
                                                Approval.fingerprint == fp))
    if existing is not None:
        return existing
    ap = Approval(tenant_id=task.tenant_id, run_id=task.run_id, task_id=task.id,
                  category=action.category.value, description=action.description[:5000],
                  amount_cents=action.amount_cents, fingerprint=fp, reason=reason)
    db.add(ap)
    db.flush()
    return ap


def reserve(db: Session, task: Task, amount_cents: int, description: str,
            idempotency_key: str) -> LedgerEntry:
    """Hold money for a task. Raises ApprovalRequired when outside policy/budget."""
    existing = db.scalar(select(LedgerEntry).where(
        LedgerEntry.tenant_id == task.tenant_id, LedgerEntry.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    # Serialize all money decisions for this run.
    run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == task.run_id).with_for_update())
    enforce(db, task, run, Action(Category.SPEND, description, amount_cents))
    entry = LedgerEntry(tenant_id=task.tenant_id, run_id=run.id, task_id=task.id, kind="reserve",
                        amount_cents=amount_cents, description=description,
                        idempotency_key=idempotency_key)
    db.add(entry)
    db.flush()
    return entry


def settle(db: Session, reservation_id: uuid.UUID, actual_cents: int, description: str,
           idempotency_key: str) -> None:
    """Record what was actually spent and release the rest of the reservation."""
    res = db.scalar(select(LedgerEntry).where(LedgerEntry.id == reservation_id,
                                              LedgerEntry.kind == "reserve"))
    if res is None:
        raise LedgerError("reservation not found")
    db.scalar(select(WorkflowRun.id).where(WorkflowRun.id == res.run_id).with_for_update())
    used = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
        LedgerEntry.reservation_id == res.id)) or 0
    if used:
        return  # already settled (idempotent)
    if not 0 <= actual_cents <= res.amount_cents:
        raise LedgerError(f"actual {actual_cents}c outside reservation {res.amount_cents}c")
    common = dict(tenant_id=res.tenant_id, run_id=res.run_id, task_id=res.task_id,
                  reservation_id=res.id)
    if actual_cents:
        db.add(LedgerEntry(**common, kind="commit", amount_cents=actual_cents,
                           description=description, idempotency_key=f"{idempotency_key}:commit"))
    if res.amount_cents - actual_cents:
        db.add(LedgerEntry(**common, kind="release", amount_cents=res.amount_cents - actual_cents,
                           description=f"unused: {description}",
                           idempotency_key=f"{idempotency_key}:release"))
    db.flush()


def audit(db: Session, tenant_id, actor, action: str, entity_type: str, entity_id, **data) -> None:
    db.add(AuditEvent(tenant_id=tenant_id, actor_user_id=actor, action=action,
                      entity_type=entity_type, entity_id=entity_id, data=data))


def decide_approval(db: Session, approval: Approval, user_id: uuid.UUID, approve: bool,
                    note: str | None) -> Approval:
    """A human decision. Approve: the task goes back to the queue and re-runs with the
    approval on file. Reject: the task and run fail. Only callable through the API by
    an owner/admin; the worker DB role can't update approvals at all."""
    if approval.status != "pending":
        raise CommandError(409, "already_decided", f"approval is already {approval.status}")
    run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == approval.run_id)
                    .with_for_update())
    task = db.scalar(select(Task).where(Task.id == approval.task_id).with_for_update())
    if TaskState(run.status) in TERMINAL_TASK_STATES:
        raise CommandError(409, "run_finished", f"run is already {run.status}")
    approval.status = "approved" if approve else "rejected"
    approval.decided_by = user_id
    approval.decided_at = func.now()
    approval.decision_note = note
    if approve:
        if task.status == TaskState.WAITING_APPROVAL.value:
            task.status = TaskState.QUEUED.value
            task.next_attempt_at = None
        if run.status == TaskState.WAITING_APPROVAL.value:
            transition(db, run, TaskState.RUNNING)
    else:
        task.status = TaskState.FAILED.value
        task.error = f"rejected by owner: {note or 'no reason given'}"
        task.finished_at = func.now()
        transition(db, run, TaskState.FAILED)
        for other in db.scalars(select(Task).where(Task.run_id == run.id, Task.id != task.id)):
            if TaskState(other.status) not in TERMINAL_TASK_STATES:
                other.status = TaskState.CANCELED.value
    db.add(RunEvent(tenant_id=run.tenant_id, run_id=run.id, type="approval.decided",
                    data={"approval_id": str(approval.id), "decision": approval.status,
                          "task_id": str(task.id)}))
    audit(db, run.tenant_id, user_id, f"approval.{approval.status}", "Approval", approval.id,
          category=approval.category, amount_cents=approval.amount_cents, note=note)
    return approval
