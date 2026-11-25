"""Approvals inbox, decisions, autonomy levels, run ledger (phase 07)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from .deps import DB, TenantMember, TenantWriter
from .governance import audit, budget_summary, decide_approval
from .models import Approval, Business, LedgerEntry, WorkflowRun
from .runs import CommandError

router = APIRouter()


class ApprovalOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    run_id: uuid.UUID
    task_id: uuid.UUID
    category: str
    description: str
    amount_cents: int
    reason: str
    status: str
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


class AutonomyIn(BaseModel):
    level: int = Field(ge=0, le=3)


class LedgerEntryOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    task_id: uuid.UUID | None
    reservation_id: uuid.UUID | None
    kind: str
    amount_cents: int
    description: str
    created_at: datetime


class LedgerOut(BaseModel):
    summary: dict
    entries: list[LedgerEntryOut]


@router.get("/tenants/{tenant_id}/approvals", response_model=list[ApprovalOut])
def list_approvals(
    tenant_id: uuid.UUID, member: TenantMember, db: DB,
    status: Annotated[Literal["pending", "approved", "rejected"] | None, Query()] = None,
) -> list[Approval]:
    q = select(Approval).where(Approval.tenant_id == tenant_id)
    if status:
        q = q.where(Approval.status == status)
    return list(db.scalars(q.order_by(Approval.created_at.desc()).limit(200)))


@router.post("/tenants/{tenant_id}/approvals/{approval_id}/decision",
             response_model=ApprovalOut)
def decide(tenant_id: uuid.UUID, approval_id: uuid.UUID, body: DecisionIn,
           writer: TenantWriter, db: DB) -> ApprovalOut:
    ap = db.scalar(select(Approval).where(Approval.id == approval_id,
                                          Approval.tenant_id == tenant_id).with_for_update())
    if ap is None:
        raise HTTPException(404, "approval not found")
    try:
        decide_approval(db, ap, writer.user_id, body.decision == "approve", body.note)
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.flush()
    db.refresh(ap)
    out = ApprovalOut.model_validate(ap)
    db.commit()
    return out


@router.put("/tenants/{tenant_id}/businesses/{business_id}/autonomy")
def set_autonomy(tenant_id: uuid.UUID, business_id: uuid.UUID, body: AutonomyIn,
                 writer: TenantWriter, db: DB) -> dict:
    if writer.role != "owner":
        raise HTTPException(403, "only the owner can change autonomy levels")
    b = db.scalar(select(Business).where(Business.id == business_id,
                                         Business.tenant_id == tenant_id).with_for_update())
    if b is None:
        raise HTTPException(404, "business not found")
    old, b.autonomy_level = b.autonomy_level, body.level
    audit(db, tenant_id, writer.user_id, "business.autonomy_changed", "Business", b.id,
          old=old, new=body.level)
    db.commit()
    return {"business_id": str(b.id), "autonomy_level": body.level, "previous": old}


@router.get("/tenants/{tenant_id}/runs/{run_id}/ledger", response_model=LedgerOut)
def run_ledger(tenant_id: uuid.UUID, run_id: uuid.UUID, member: TenantMember,
               db: DB) -> LedgerOut:
    run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id,
                                              WorkflowRun.tenant_id == tenant_id))
    if run is None:
        raise HTTPException(404, "run not found")
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.run_id == run.id)
                         .order_by(LedgerEntry.created_at))
    return LedgerOut(summary=budget_summary(db, run),
                     entries=[LedgerEntryOut.model_validate(e) for e in entries])
