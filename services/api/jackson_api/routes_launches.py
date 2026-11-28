"""Launch approvals API (phase 21, ADR-032). Only the OWNER approves, edits, resumes or
revokes a launch; Jackson can only propose one and stop one."""

import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from . import launches as L
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import Business, Launch, LaunchSignal, Opportunity, WorkflowRun
from .runs import CommandError, control_run, create_run

router = APIRouter()


def _owner(m):
    if m.role != "owner":
        raise HTTPException(403, "only the owner can approve or change a launch")


class Channels(BaseModel):
    shopify_publish: bool = False
    social_accounts: list[str] = Field(default_factory=list, max_length=20,
                                       description="e.g. instagram, tiktok")
    email_lists: list[str] = Field(default_factory=list, max_length=20,
                                   description="campaign list names (audience.list)")
    ads: bool = False
    ad_platforms: list[Literal["meta", "tiktok"]] = Field(
        default_factory=list, description="which ad platforms may spend (needs ads=true)")

    def stored(self) -> dict:
        return {"shopify_publish": self.shopify_publish,
                "social": {"accounts": sorted({a.strip().lower() for a in self.social_accounts
                                               if a.strip()})},
                "email": {"lists": sorted({x.strip() for x in self.email_lists if x.strip()})},
                "ads": self.ads,
                "ad_platforms": sorted(set(self.ad_platforms)) if self.ads else []}


class KillRules(BaseModel):
    max_cac_cents: int | None = Field(default=None, gt=0)
    zero_sales_after_days: int | None = Field(default=None, ge=1, le=90)


class Scope(BaseModel):
    total_budget_cents: int = Field(ge=0, le=100_000_000)
    daily_cap_cents: int = Field(ge=0, le=100_000_000)
    duration_days: int = Field(ge=1, le=90)
    channels: Channels = Field(default_factory=Channels)
    kill_rules: KillRules = Field(default_factory=KillRules)

    @model_validator(mode="after")
    def _caps(self):
        if self.daily_cap_cents > self.total_budget_cents:
            raise ValueError("daily cap can't be more than the total budget")
        return self


class LaunchIn(Scope):
    business_id: uuid.UUID
    opportunity_id: uuid.UUID | None = None
    title: str = Field(min_length=3, max_length=200)
    plan: str = Field(default="", max_length=20_000)


def _out(db, la: Launch, with_metrics=True) -> dict:
    ch = la.channels or {}
    d = {"id": str(la.id), "business_id": str(la.business_id),
         "opportunity_id": str(la.opportunity_id) if la.opportunity_id else None,
         "title": la.title, "plan": la.plan, "proposed_by": la.proposed_by,
         "status": la.status, "status_reason": la.status_reason,
         "total_budget_cents": la.total_budget_cents, "daily_cap_cents": la.daily_cap_cents,
         "channels": {"shopify_publish": bool(ch.get("shopify_publish")),
                      "social_accounts": (ch.get("social") or {}).get("accounts", []),
                      "email_lists": (ch.get("email") or {}).get("lists", []),
                      "ads": bool(ch.get("ads")),
                      "ad_platforms": ch.get("ad_platforms") or []},
         "kill_rules": la.kill_rules, "starts_at": la.starts_at, "ends_at": la.ends_at,
         "version": la.version, "approved_by": str(la.approved_by) if la.approved_by else None,
         "approved_at": la.approved_at, "created_at": la.created_at}
    if with_metrics and la.status not in ("proposed", "rejected"):
        d["metrics"] = L.metrics(db, la)
    return d


def _get(db, tenant_id, lid) -> Launch:
    la = db.get(Launch, lid)
    if la is None or la.tenant_id != tenant_id:
        raise HTTPException(404, "launch not found")
    return la


def _apply_scope(la: Launch, s: Scope, start: bool) -> None:
    la.total_budget_cents, la.daily_cap_cents = s.total_budget_cents, s.daily_cap_cents
    la.channels, la.kill_rules = s.channels.stored(), s.kill_rules.model_dump(exclude_none=True)
    if start:
        la.starts_at = L.now()
    la.ends_at = la.starts_at + timedelta(days=s.duration_days)


@router.get("/tenants/{tenant_id}/launches")
def list_launches(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[dict]:
    rows = db.scalars(select(Launch).where(Launch.tenant_id == tenant_id)
                      .order_by(Launch.created_at.desc()).limit(100)).all()
    for la in rows:  # keep statuses honest whenever the owner looks
        L.check_health(db, la)
    out = [_out(db, la) for la in rows]
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/launches/{lid}")
def get_launch(tenant_id: uuid.UUID, lid: uuid.UUID, member: TenantMember, db: DB) -> dict:
    la = _get(db, tenant_id, lid)
    L.check_health(db, la)
    out = _out(db, la)
    db.commit()
    return out


@router.post("/tenants/{tenant_id}/launches", status_code=201)
def create_launch(tenant_id: uuid.UUID, body: LaunchIn, writer: TenantWriter, db: DB) -> dict:
    """The owner's ONE approval for a launch: scope, budget, caps, window, kill rules."""
    _owner(writer)
    if db.scalar(select(Business.id).where(Business.id == body.business_id,
                                           Business.tenant_id == tenant_id)) is None:
        raise HTTPException(422, "business not found")
    if body.opportunity_id and db.scalar(select(Opportunity.id).where(
            Opportunity.id == body.opportunity_id, Opportunity.tenant_id == tenant_id)) is None:
        raise HTTPException(422, "opportunity not found")
    la = Launch(tenant_id=tenant_id, business_id=body.business_id,
                opportunity_id=body.opportunity_id, title=body.title, plan=body.plan,
                proposed_by="owner", status="active", approved_by=writer.user_id,
                approved_at=func.now(), version=1)
    _apply_scope(la, body, start=True)
    db.add(la)
    db.flush()
    audit(db, tenant_id, writer.user_id, "launch.approved", "Launch", la.id,
          scope=_scope_record(la))
    db.flush()
    db.refresh(la)
    out = _out(db, la)
    db.commit()
    return out


def _scope_record(la: Launch) -> dict:
    return {"total_budget_cents": la.total_budget_cents, "daily_cap_cents": la.daily_cap_cents,
            "channels": la.channels, "kill_rules": la.kill_rules,
            "ends_at": la.ends_at.isoformat() if la.ends_at else None, "version": la.version}


@router.post("/tenants/{tenant_id}/launches/{lid}/approve")
def approve_proposal(tenant_id: uuid.UUID, lid: uuid.UUID, body: Scope, writer: TenantWriter,
                     db: DB) -> dict:
    """Approve a launch Jackson proposed, with the owner's scope."""
    _owner(writer)
    la = _get(db, tenant_id, lid)
    if la.status != "proposed":
        raise HTTPException(409, f"launch is {la.status}")
    la.status, la.approved_by, la.approved_at = "active", writer.user_id, func.now()
    _apply_scope(la, body, start=True)
    audit(db, tenant_id, writer.user_id, "launch.approved", "Launch", la.id,
          scope=_scope_record(la))
    db.flush()
    db.refresh(la)
    out = _out(db, la)
    db.commit()
    return out


@router.post("/tenants/{tenant_id}/launches/{lid}/reject")
def reject_proposal(tenant_id: uuid.UUID, lid: uuid.UUID, writer: TenantWriter, db: DB) -> dict:
    _owner(writer)
    la = _get(db, tenant_id, lid)
    if la.status != "proposed":
        raise HTTPException(409, f"launch is {la.status}")
    la.status = "rejected"
    audit(db, tenant_id, writer.user_id, "launch.rejected", "Launch", la.id)
    out = _out(db, la, with_metrics=False)
    db.commit()
    return out


@router.patch("/tenants/{tenant_id}/launches/{lid}")
def edit_scope(tenant_id: uuid.UUID, lid: uuid.UUID, body: Scope, writer: TenantWriter,
               db: DB) -> dict:
    """Owner edits the scope (raise/lower budget, change channels, extend). New version,
    old and new scope both kept in the audit log."""
    _owner(writer)
    la = _get(db, tenant_id, lid)
    if la.status not in ("active", "paused"):
        raise HTTPException(409, f"launch is {la.status}")
    before = _scope_record(la)
    _apply_scope(la, body, start=False)
    la.version += 1
    audit(db, tenant_id, writer.user_id, "launch.scope_changed", "Launch", la.id,
          before=before, after=_scope_record(la))
    db.flush()
    db.refresh(la)
    out = _out(db, la)
    db.commit()
    return out


def _runs(db, la) -> list[WorkflowRun]:
    return db.scalars(select(WorkflowRun).where(
        WorkflowRun.launch_id == la.id,
        WorkflowRun.status.not_in(("succeeded", "failed", "canceled")))).all()


class StartIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    objective: str | None = Field(default=None, max_length=2000)


@router.post("/tenants/{tenant_id}/launches/{lid}/start", status_code=202)
def start(tenant_id: uuid.UUID, lid: uuid.UUID, body: StartIn, writer: TenantWriter,
          db: DB) -> dict:
    """Start Jackson's work for the launch: a run whose every action is checked against
    the launch scope (in scope: runs alone; out of scope: approvals inbox)."""
    _owner(writer)
    la = _get(db, tenant_id, lid)
    if la.status != "active":
        raise HTTPException(409, f"launch is {la.status}")
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "EXECUTE", "business_id": la.business_id,
            "objective": body.objective or f"Launch: {la.title}. {la.plan}"[:2000],
            "idempotency_key": body.idempotency_key}, launch_id=la.id)
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id)}


class SignalIn(BaseModel):
    kind: Literal["sale", "refund", "send", "bounce", "complaint", "platform_error"]
    quantity: int = Field(default=1, ge=0, le=100_000)
    amount_cents: int = Field(default=0, ge=0, le=100_000_000)
    external_ref: str | None = Field(default=None, max_length=300)


@router.post("/tenants/{tenant_id}/launches/{lid}/signals", status_code=201)
def add_signal(tenant_id: uuid.UUID, lid: uuid.UUID, body: SignalIn, writer: TenantWriter,
               db: DB) -> dict:
    """Record an outcome by hand (e.g. a sale) until a connector reports it."""
    la = _get(db, tenant_id, lid)
    db.add(LaunchSignal(tenant_id=tenant_id, launch_id=la.id, source="owner",
                        **body.model_dump()))
    db.flush()
    status = L.check_health(db, la)
    db.commit()
    return {"recorded": True, "launch_status": la.status, "changed_to": status}


def propose_handler(ctx):
    """Jackson proposes a launch (status 'proposed', no scope). The owner sets the scope
    and approves it; the worker role can't create anything but a proposal."""
    from .worker import TaskResult
    from .worker_errors import NonRetryableError

    inp = ctx.input or {}
    with ctx.db_session() as db:
        opp = db.get(Opportunity, uuid.UUID(inp["opportunity_id"])) \
            if inp.get("opportunity_id") else None
        if opp is not None and opp.tenant_id != ctx.tenant_id:
            opp = None
        business_id = (opp.business_id if opp and opp.business_id else ctx.business_id)
        if business_id is None:
            raise NonRetryableError("a launch needs a business")
        la = Launch(tenant_id=ctx.tenant_id, business_id=business_id,
                    opportunity_id=opp.id if opp else None,
                    title=(inp.get("title") or (f"Launch {opp.title}" if opp else "Launch"))[:200],
                    plan=(inp.get("plan") or "")[:20_000], proposed_by="jackson",
                    status="proposed", version=1)
        db.add(la)
        db.flush()
        lid = str(la.id)
        db.commit()
    return TaskResult({"launch_id": lid, "status": "proposed",
                       "next": "the owner sets budget, caps, channels and kill rules"})


# Declared last so the fixed paths above (approve, reject, start, signals) win.
@router.post("/tenants/{tenant_id}/launches/{lid}/{action}")
def control(tenant_id: uuid.UUID, lid: uuid.UUID, action: Literal["pause", "resume", "revoke"],
            writer: TenantWriter, db: DB) -> dict:
    _owner(writer)
    la = _get(db, tenant_id, lid)
    if action == "pause":
        if la.status != "active":
            raise HTTPException(409, f"launch is {la.status}")
        la.status, la.status_reason = "paused", "paused by the owner"
    elif action == "resume":
        if la.status not in ("paused", "killed", "completed"):
            raise HTTPException(409, f"launch is {la.status}")
        if la.ends_at and L.now() >= la.ends_at:
            raise HTTPException(409, "the launch window has ended; extend it first")
        la.status, la.status_reason = "active", None
    else:  # revoke: ends the launch and cancels its unfinished runs
        if la.status in ("revoked", "rejected", "expired"):
            raise HTTPException(409, f"launch is {la.status}")
        la.status, la.status_reason = "revoked", "revoked by the owner"
        from .contracts import CommandType

        for run in _runs(db, la):
            control_run(db, run, CommandType.STOP, writer.user_id)
    audit(db, tenant_id, writer.user_id, f"launch.{action}", "Launch", la.id)
    db.flush()
    out = _out(db, la)
    db.commit()
    return out
