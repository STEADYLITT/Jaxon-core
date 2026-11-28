"""Clip scouting API (ADR-052)."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .connectors import clips as K
from .deps import DB, TenantWriter

router = APIRouter(tags=["clips"])


class ScoutIn(BaseModel):
    youtube_query: str = Field(default="", max_length=200)
    youtube_channel_ids: list[str] = Field(default_factory=list, max_length=10)
    twitch_logins: list[str] = Field(default_factory=list, max_length=20)
    days: int = Field(default=7, ge=1, le=60)
    limit: int = Field(default=20, ge=1, le=50)
    business_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/clips/scout", status_code=202)
def scout(tenant_id: uuid.UUID, body: ScoutIn, writer: TenantWriter, db: DB) -> dict:
    from .runs import CommandError, create_run

    wants_yt = bool(body.youtube_query or body.youtube_channel_ids)
    if not (wants_yt or body.twitch_logins):
        raise HTTPException(422, "give a YouTube search, YouTube channels or Twitch channels")
    if wants_yt and not K.youtube_configured():
        raise HTTPException(409, "set YOUTUBE_API_KEY in Railway")
    if body.twitch_logins and not K.twitch_configured():
        raise HTTPException(409, "set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in Railway")
    inp = body.model_dump(exclude={"idempotency_key", "business_id"})
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": "Scout clippable moments",
            "idempotency_key": f"clips-{body.idempotency_key}",
            "business_id": body.business_id},
            first_task={"kind": "clip_scout", "title": "Scout clippable moments",
                        "tool_scope": [], "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}
