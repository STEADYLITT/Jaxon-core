"""Follower analytics (ADR-058): take today's real follower counts now (after that the
worker reads them once a day), and read the growth."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .deps import DB, TenantMember, TenantWriter

router = APIRouter(tags=["analytics"])


class SnapIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/analytics/followers/snapshot", status_code=202)
def snapshot(tenant_id: uuid.UUID, body: SnapIn, writer: TenantWriter, db: DB) -> dict:
    from .connectors import analytics as A
    from .runs import CommandError, create_run

    if not A.enabled():
        raise HTTPException(409, "set PLATFORM_ANALYTICS (e.g. youtube,instagram,tiktok) and "
                                 "each platform's variables in Railway")
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "REPORT", "objective": "Read follower counts",
            "idempotency_key": f"followers-{body.idempotency_key}"},
            first_task={"kind": "follower_snapshot", "title": "Read follower counts",
                        "tool_scope": [], "input": {}})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}


@router.get("/tenants/{tenant_id}/analytics/followers")
def followers(tenant_id: uuid.UUID, member: TenantMember, db: DB, days: int = 30) -> dict:
    from .followers import growth

    g = growth(db, tenant_id, max(1, min(days, 365)))
    return {**g, "first_day": g["first_day"].isoformat() if g["first_day"] else None,
            "accounts": [{**a, "first_on": a["first_on"].isoformat(),
                          "latest_on": a["latest_on"].isoformat()} for a in g["accounts"]]}
