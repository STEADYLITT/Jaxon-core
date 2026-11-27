"""Whop API (ADR-041): status and members, a sync, product/plan -> business links, course
checkout links, and the signed Whop webhook."""

import json
import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .connectors import whop as W
from .db import bind_tenant
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import Business, Course, WhopLink, WhopMember
from .whop_sync import record_payment, upsert_member

router = APIRouter(tags=["whop"])


def _link(x: WhopLink) -> dict:
    return {"id": str(x.id), "business_id": str(x.business_id),
            "course_id": str(x.course_id) if x.course_id else None,
            "product_id": x.product_id, "variant_id": x.variant_id,
            "purchase_url": x.purchase_url, "created_at": x.created_at.isoformat()}


@router.get("/tenants/{tenant_id}/whop")
def whop_status(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    by_status = dict(db.execute(select(WhopMember.status, func.count()).where(
        WhopMember.tenant_id == tenant_id).group_by(WhopMember.status)).all())
    links = db.scalars(select(WhopLink).where(WhopLink.tenant_id == tenant_id)
                       .order_by(WhopLink.created_at.desc()).limit(200)).all()
    return {"connected": W.configured(),
            "webhook_secret_set": bool(os.environ.get("WHOP_WEBHOOK_SECRET")),
            "members_by_status": by_status,
            "members_with_access": sum(n for s, n in by_status.items() if s in W.ACCESS),
            "links": [_link(x) for x in links],
            "webhook_url": f"{os.environ.get('PUBLIC_BASE_URL', '')}/webhooks/whop/{tenant_id}"}


class SyncIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/whop/sync", status_code=202)
def whop_sync(tenant_id: uuid.UUID, body: SyncIn, writer: TenantWriter, db: DB) -> dict:
    from .runs import CommandError, create_run

    if not W.configured():
        raise HTTPException(409, "Whop isn't connected: set WHOP_API_KEY in Railway")
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "REPORT", "objective": "Sync members and sales from Whop",
            "idempotency_key": body.idempotency_key},
            first_task={"kind": "whop_sync", "title": "Sync members and sales from Whop",
                        "tool_scope": [], "input": {}})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}


class LinkIn(BaseModel):
    business_id: uuid.UUID
    product_id: str | None = Field(default=None, pattern=r"^prod_[A-Za-z0-9]+$")
    variant_id: str | None = Field(default=None, pattern=r"^plan_[A-Za-z0-9]+$")
    course_id: uuid.UUID | None = None


@router.post("/tenants/{tenant_id}/whop/links", status_code=201)
def whop_link(tenant_id: uuid.UUID, body: LinkIn, writer: TenantWriter, db: DB) -> dict:
    """Tie a Whop product or plan to a business. With a course and a plan, Jackson also asks
    Whop for a checkout link for that plan (no charge; Whop runs the checkout) and the
    course's sales page uses it. Publishing the page is still a launch decision."""
    if not (body.product_id or body.variant_id):
        raise HTTPException(422, "give a Whop product_id (prod_...) or plan id (plan_...)")
    biz = db.get(Business, body.business_id)
    if biz is None or biz.tenant_id != tenant_id:
        raise HTTPException(404, "business not found")
    checkout = None
    if body.course_id:
        c = db.get(Course, body.course_id)
        if c is None or c.tenant_id != tenant_id:
            raise HTTPException(404, "course not found")
        if not body.variant_id:
            raise HTTPException(422, "a course checkout needs the Whop plan id (plan_...)")
        if not W.configured():
            raise HTTPException(409, "Whop isn't connected: set WHOP_API_KEY in Railway")
        try:
            checkout = W.client().checkout(body.variant_id, {
                "jackson_business_id": str(biz.id), "jackson_course_id": str(c.id)})
        except Exception as e:  # noqa: BLE001 - shown (scrubbed) to the owner
            from .connections import scrub
            raise HTTPException(502, scrub(str(e))) from None
    x = WhopLink(tenant_id=tenant_id, business_id=biz.id, course_id=body.course_id,
                 product_id=body.product_id, variant_id=body.variant_id,
                 checkout_id=(checkout or {}).get("id"),
                 purchase_url=(checkout or {}).get("purchase_url"), created_by=writer.user_id)
    db.add(x)
    db.flush()
    audit(db, tenant_id, writer.user_id, "whop.link", "whop_link", x.id,
          business_id=str(biz.id), product_id=body.product_id, variant_id=body.variant_id,
          course_id=str(body.course_id) if body.course_id else None,
          checkout=bool(checkout))
    db.commit()
    return _link(x)


def checkout_url(db, tenant_id, course_id) -> str | None:
    return db.scalar(select(WhopLink.purchase_url).where(
        WhopLink.tenant_id == tenant_id, WhopLink.course_id == course_id,
        WhopLink.purchase_url.is_not(None)).order_by(WhopLink.created_at.desc()).limit(1))


@router.post("/webhooks/whop/{tenant_id}", include_in_schema=False)
async def whop_webhook(tenant_id: uuid.UUID, request: Request, db: DB):
    """Sales, refunds and membership changes from Whop. Signed (Standard Webhooks); unsigned
    or stale calls are refused."""
    secret = os.environ.get("WHOP_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "WHOP_WEBHOOK_SECRET is not set")
    body = await request.body()
    if not W.verify_webhook(secret, {k.lower(): v for k, v in request.headers.items()}, body):
        raise HTTPException(401, "bad signature")
    try:
        event = json.loads(body)
        kind, data = str(event.get("type") or ""), event.get("data") or {}
        assert isinstance(data, dict)
    except (ValueError, AttributeError, AssertionError):
        raise HTTPException(400, "not a Whop event") from None
    bind_tenant(db, tenant_id)
    if kind == "payment.succeeded":
        r = record_payment(db, tenant_id, data)
        db.commit()
        return {"ok": True, "recorded": "sale" if r["sale"] else "already recorded"}
    if kind in ("membership.activated", "membership.updated", "membership.deactivated"):
        upsert_member(db, tenant_id, data)
        db.commit()
        return {"ok": True, "recorded": "membership"}
    if kind == "refund.created":
        pay = data.get("payment") if isinstance(data.get("payment"), dict) else {}
        pid = pay.get("id") or data.get("payment_id")
        if pid and W.configured():
            try:
                r = record_payment(db, tenant_id, W.client().payment(str(pid)))
            except Exception:  # noqa: BLE001 - the next sync picks it up
                return {"ok": True, "recorded": "refund pending next sync"}
            db.commit()
            return {"ok": True, "recorded": f"refund {r['refund_cents']} cents"}
        return {"ok": True, "recorded": "refund pending next sync"}
    return {"ok": True, "ignored": True}
