"""Opportunity board API (phase 13)."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .deps import DB, TenantMember, TenantWriter
from .economics import EconomicsIn, attach, calculate
from .governance import audit
from .models import Business, Opportunity
from .opportunities import upsert
from .runs import CommandError, create_run

router = APIRouter()


class SignalIn(BaseModel):
    type: str
    value: float | bool
    source_url: str
    observed_at: str | None = None
    note: str = ""


class OpportunityIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    business_id: uuid.UUID | None = None
    category: str | None = None
    summary: str | None = Field(default=None, max_length=5000)
    signals: list[SignalIn] = Field(default_factory=list)


class OpportunityOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    business_id: uuid.UUID | None
    title: str
    category: str | None
    summary: str | None
    score: float
    uncertainty: float
    scores: dict
    signals: list[dict]
    sources: list[str]
    economics: dict | None
    status: str
    created_by: str
    brief: str
    created_at: datetime
    updated_at: datetime


def _check_business(db, tenant_id, business_id):
    if business_id and db.scalar(select(Business.id).where(
            Business.id == business_id, Business.tenant_id == tenant_id)) is None:
        raise HTTPException(422, "business not found in this tenant")


@router.post("/tenants/{tenant_id}/opportunities", response_model=OpportunityOut)
def add(tenant_id: uuid.UUID, body: OpportunityIn, writer: TenantWriter, db: DB):
    _check_business(db, tenant_id, body.business_id)
    opp, created = upsert(db, tenant_id=tenant_id, title=body.title,
                          signals=[s.model_dump() for s in body.signals],
                          business_id=body.business_id, summary=body.summary,
                          category=body.category, created_by="owner")
    audit(db, tenant_id, writer.user_id, "opportunity.added" if created else
          "opportunity.enriched", "Opportunity", opp.id, signals=len(body.signals))
    db.refresh(opp)
    out = OpportunityOut.model_validate(opp)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/opportunities", response_model=list[OpportunityOut])
def board(tenant_id: uuid.UUID, member: TenantMember, db: DB,
          business_id: uuid.UUID | None = None, status: str | None = None):
    q = select(Opportunity).where(Opportunity.tenant_id == tenant_id)
    if business_id:
        q = q.where(Opportunity.business_id == business_id)
    if status:
        q = q.where(Opportunity.status == status)
    return list(db.scalars(q.order_by(Opportunity.score.desc(),
                                      Opportunity.uncertainty.asc()).limit(200)))


@router.get("/tenants/{tenant_id}/opportunities/{opp_id}", response_model=OpportunityOut)
def get_one(tenant_id: uuid.UUID, opp_id: uuid.UUID, member: TenantMember, db: DB):
    opp = db.scalar(select(Opportunity).where(Opportunity.id == opp_id,
                                              Opportunity.tenant_id == tenant_id))
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    return opp


class StatusIn(BaseModel):
    status: Literal["new", "researching", "validated", "rejected", "pursuing"]
    note: str | None = None


@router.post("/tenants/{tenant_id}/opportunities/{opp_id}/status",
             response_model=OpportunityOut)
def set_status(tenant_id: uuid.UUID, opp_id: uuid.UUID, body: StatusIn, writer: TenantWriter,
               db: DB):
    opp = get_one(tenant_id, opp_id, writer, db)
    old, opp.status = opp.status, body.status
    audit(db, tenant_id, writer.user_id, "opportunity.status", "Opportunity", opp.id,
          old=old, new=body.status, note=body.note)
    db.flush()
    db.refresh(opp)
    out = OpportunityOut.model_validate(opp)
    db.commit()
    return out


class ResearchIn(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    urls: list[str] = Field(min_length=1, max_length=10)
    business_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/opportunities/research", status_code=202)
def research(tenant_id: uuid.UUID, body: ResearchIn, writer: TenantWriter, db: DB) -> dict:
    _check_business(db, tenant_id, body.business_id)
    payload = {"type": "PLAN", "objective": f"Research: {body.query}",
               "idempotency_key": body.idempotency_key, "business_id": body.business_id}
    try:
        run, created = create_run(db, tenant_id, writer.user_id, payload, first_task={
            "kind": "opportunity_research", "title": f"Research: {body.query[:250]}",
            "input": {"query": body.query, "urls": body.urls}})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id), "created": created}


@router.post("/tenants/{tenant_id}/economics/calculate")
def calc(tenant_id: uuid.UUID, body: EconomicsIn, member: TenantMember) -> dict:
    """Pure calculator: nothing is saved."""
    return calculate(body)


@router.post("/tenants/{tenant_id}/opportunities/{opp_id}/economics",
             response_model=OpportunityOut)
def save_economics(tenant_id: uuid.UUID, opp_id: uuid.UUID, body: EconomicsIn,
                   writer: TenantWriter, db: DB):
    opp = get_one(tenant_id, opp_id, writer, db)
    result = attach(db, opp, body, by=f"user:{writer.user_id}")
    audit(db, tenant_id, writer.user_id, "opportunity.economics", "Opportunity", opp.id,
          inputs=body.model_dump(), margin_pct=result["contribution_margin_pct"])
    db.refresh(opp)
    out = OpportunityOut.model_validate(opp)
    db.commit()
    return out
