"""Paid ads API (ADR-048): draft a paused campaign, switch it on inside an active Launch,
pause it any time."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .connectors import ads as A
from .deps import DB, TenantMember, TenantWriter
from .models import AdCampaign, Launch

router = APIRouter(tags=["ads"])


def _row(c: AdCampaign) -> dict:
    return {"id": str(c.id), "platform": c.platform, "name": c.name, "status": c.status,
            "objective": c.objective, "daily_budget_cents": c.daily_budget_cents,
            "launch_id": str(c.launch_id) if c.launch_id else None,
            "external_campaign_id": c.external_campaign_id,
            "business_id": str(c.business_id) if c.business_id else None}


@router.get("/tenants/{tenant_id}/ads")
def list_ads(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    rows = db.scalars(select(AdCampaign).where(AdCampaign.tenant_id == tenant_id)
                      .order_by(AdCampaign.created_at.desc()).limit(200)).all()
    return {"meta_connected": A.meta_configured(), "tiktok_connected": A.tiktok_configured(),
            "campaigns": [_row(c) for c in rows]}


def _start(db, tenant_id, writer, kind, title, inp, key, business_id=None, launch_id=None):
    from .runs import CommandError, create_run
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": title[:300], "idempotency_key": f"{kind}-{key}",
            "business_id": business_id},
            first_task={"kind": kind, "title": title[:200], "tool_scope": [], "input": inp},
            launch_id=launch_id)
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}


class DraftIn(BaseModel):
    platform: Literal["meta", "tiktok"]
    name: str = Field(min_length=3, max_length=200)
    objective: str = Field(default="OUTCOME_TRAFFIC", max_length=40)
    daily_budget_cents: int = Field(ge=100, le=1_000_000)
    countries: list[str] = Field(default_factory=lambda: ["US"], max_length=25)
    copy: str = Field(default="", max_length=2000)
    special_categories: list[str] = Field(default_factory=list)
    business_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/ads/drafts", status_code=202)
def draft(tenant_id: uuid.UUID, body: DraftIn, writer: TenantWriter, db: DB) -> dict:
    """A paused draft: spends nothing. TikTok objectives: TRAFFIC, REACH, VIDEO_VIEWS,
    ENGAGEMENT; Meta: OUTCOME_TRAFFIC, OUTCOME_LEADS, OUTCOME_SALES, ..."""
    inp = body.model_dump(exclude={"idempotency_key", "business_id"})
    return _start(db, tenant_id, writer, "ad_draft", f"Draft {body.platform} ad: {body.name}",
                  inp, body.idempotency_key, business_id=body.business_id)


class OneIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


def _campaign(db, tenant_id, aid) -> AdCampaign:
    c = db.get(AdCampaign, aid)
    if c is None or c.tenant_id != tenant_id:
        raise HTTPException(404, "ad campaign not found")
    return c


class ActivateIn(OneIn):
    launch_id: uuid.UUID


@router.post("/tenants/{tenant_id}/ads/{aid}/activate", status_code=202)
def activate(tenant_id: uuid.UUID, aid: uuid.UUID, body: ActivateIn, writer: TenantWriter,
             db: DB) -> dict:
    """Spend only under an approved Launch: the Launch must be active, belong to the same
    business and list this platform in its ads channel."""
    c = _campaign(db, tenant_id, aid)
    la = db.get(Launch, body.launch_id)
    if la is None or la.tenant_id != tenant_id:
        raise HTTPException(404, "launch not found")
    if la.status != "active":
        raise HTTPException(409, f"the launch is {la.status}: ads only go live inside an "
                                 "active Launch you approved")
    return _start(db, tenant_id, writer, "ad_activate", f"Turn on {c.platform} ad: {c.name}",
                  {"ad_campaign_id": str(c.id)}, body.idempotency_key,
                  business_id=c.business_id, launch_id=la.id)


@router.post("/tenants/{tenant_id}/ads/{aid}/pause", status_code=202)
def pause(tenant_id: uuid.UUID, aid: uuid.UUID, body: OneIn, writer: TenantWriter,
          db: DB) -> dict:
    c = _campaign(db, tenant_id, aid)
    return _start(db, tenant_id, writer, "ad_pause", f"Pause {c.platform} ad: {c.name}",
                  {"ad_campaign_id": str(c.id)}, body.idempotency_key,
                  business_id=c.business_id)
