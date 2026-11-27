"""Owner dashboard API (phase 19): dashboard, global stop/resume, revenue, daily reports and
the interim owner console page."""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import dashboard
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import Artifact, RevenueEntry
from .runs import CommandError, create_run

router = APIRouter()
CONSOLE = Path(__file__).parent / "console"


@router.get("/tenants/{tenant_id}/dashboard")
def get_dashboard(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    return dashboard.build(db, tenant_id)


def _owner(m):
    if m.role != "owner":
        raise HTTPException(403, "only the owner can use the global stop")


class StopIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/tenants/{tenant_id}/stop")
def global_stop(tenant_id: uuid.UUID, body: StopIn, writer: TenantWriter, db: DB) -> dict:
    """GLOBAL STOP: revoke new work, cancel unfinished runs, stand down scheduled outward
    actions, and return what to reconcile."""
    _owner(writer)
    out = dashboard.global_stop(db, tenant_id, writer.user_id, body.reason)
    db.commit()
    return out


@router.post("/tenants/{tenant_id}/resume")
def resume(tenant_id: uuid.UUID, writer: TenantWriter, db: DB) -> dict:
    """Allow new work again. Canceled runs stay canceled; start them again on purpose."""
    _owner(writer)
    dashboard.resume(db, tenant_id, writer.user_id)
    db.commit()
    return {"stopped": False}


class RevenueIn(BaseModel):
    kind: Literal["estimate", "actual", "refund"]
    amount_cents: int = Field(ge=0, le=10_000_000_000)
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
                        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m"))
    business_id: uuid.UUID | None = None
    reference: str = Field(default="", max_length=300,
                           description="invoice/order/payout id, or how the estimate was made")


@router.post("/tenants/{tenant_id}/revenue", status_code=201)
def add_revenue(tenant_id: uuid.UUID, body: RevenueIn, writer: TenantWriter, db: DB) -> dict:
    """Manual entry until a Shopify/Whop/Stripe connector reports real numbers. Append-only:
    to correct, add a refund or a new estimate."""
    if body.kind == "actual" and len(body.reference.strip()) < 3:
        raise HTTPException(422, "actual revenue needs a reference (order, invoice or payout)")
    e = RevenueEntry(tenant_id=tenant_id, recorded_by=writer.user_id, source="manual",
                     **body.model_dump())
    db.add(e)
    db.flush()
    audit(db, tenant_id, writer.user_id, f"revenue.{body.kind}", "RevenueEntry", e.id,
          amount_cents=body.amount_cents, period=body.period)
    if body.kind == "actual":  # only real, referenced revenue lights the gold beam
        from .city import emit

        emit(db, tenant_id, "revenue.recorded", from_=("ceo", "steady"),
             payload={"amount_cents": body.amount_cents, "source": "manual",
                      "reference": body.reference[:300], "entry_id": str(e.id)})
    out = {"id": str(e.id), **body.model_dump(mode="json"), "source": "manual"}
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/revenue")
def list_revenue(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[dict]:
    rows = db.scalars(select(RevenueEntry).where(RevenueEntry.tenant_id == tenant_id)
                      .order_by(RevenueEntry.created_at.desc()).limit(500)).all()
    return [{"id": str(r.id), "kind": r.kind, "amount_cents": r.amount_cents,
             "period": r.period, "source": r.source, "reference": r.reference,
             "business_id": str(r.business_id) if r.business_id else None,
             "created_at": r.created_at} for r in rows]


class KeyIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/reports/daily", status_code=202)
def daily_report(tenant_id: uuid.UUID, body: KeyIn, writer: TenantWriter, db: DB) -> dict:
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "REPORT", "objective": "Daily report", "idempotency_key":
            body.idempotency_key}, first_task={"kind": "daily_report", "title": "Daily report",
                                               "tool_scope": []})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id)}


@router.get("/tenants/{tenant_id}/reports/daily/preview")
def daily_report_preview(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    """The same report, computed now, without storing it."""
    return dashboard.daily_report(db, tenant_id)


@router.get("/tenants/{tenant_id}/reports")
def list_reports(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[dict]:
    rows = db.scalars(select(Artifact).where(Artifact.tenant_id == tenant_id,
                                             Artifact.kind == "report")
                      .order_by(Artifact.created_at.desc()).limit(100)).all()
    return [{"id": str(a.id), "type": (a.meta or {}).get("type"), "run_id": str(a.run_id)
             if a.run_id else None, "created_at": a.created_at} for a in rows]


# --- Owner console -----------------------------------------------------------------------------

def _csp() -> str:
    """Strict by default. With Clerk configured, also allow Clerk's own script/API host and
    the bot-protection challenge Clerk uses (values derived from the public key)."""
    import os

    from .deploy_env import clerk_frontend_api

    fapi = clerk_frontend_api(os.environ.get("CLERK_PUBLISHABLE_KEY"))
    if not fapi:
        return ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'")
    cf = "https://challenges.cloudflare.com"
    return (f"default-src 'none'; script-src 'self' https://{fapi} {cf}; "
            f"style-src 'self' 'unsafe-inline'; connect-src 'self' https://{fapi}; "
            f"img-src 'self' data: https://img.clerk.com; frame-src {cf}; "
            "worker-src 'self' blob:; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'")


def _static(name: str, media: str) -> Response:
    return Response((CONSOLE / name).read_text(), media_type=media, headers={
        "Content-Security-Policy": _csp(), "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})


@router.get("/console", include_in_schema=False)
def console_page() -> Response:
    """Static page with no data in it. Signs in with Clerk when CLERK_PUBLISHABLE_KEY is
    set (otherwise asks for a pasted token) and reads everything through the same
    authenticated API."""
    return _static("index.html", "text/html; charset=utf-8")


@router.get("/console/config.json", include_in_schema=False)
def console_config() -> dict:
    """Public values only: the Clerk publishable key is designed to be shown to browsers."""
    import os

    try:
        events_timeout = min(max(float(os.environ.get("CONSOLE_EVENTS_TIMEOUT", "25")), 0.1), 60)
    except ValueError:
        events_timeout = 25.0
    return {"clerk_publishable_key": os.environ.get("CLERK_PUBLISHABLE_KEY") or None,
            "events_timeout": events_timeout}


VENDOR = {"three.module.min.js", "three.core.min.js"}  # pinned three.js r180 (MIT)


@router.get("/console/vendor/{name}", include_in_schema=False)
def console_vendor(name: str) -> Response:
    if name not in VENDOR:
        raise HTTPException(404, "not found")
    r = _static(f"vendor/{name}", "text/javascript")
    r.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return r


@router.get("/console/{name}", include_in_schema=False)
def console_asset(name: str) -> Response:
    if not re.fullmatch(r"[a-z]+\.(js|css)", name) or not (CONSOLE / name).is_file():
        raise HTTPException(404, "not found")
    return _static(name, "text/javascript" if name.endswith(".js") else "text/css")
