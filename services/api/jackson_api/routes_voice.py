"""AI voice API (ADR-042): list the account's voices, see the cost, ask for narration."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .connectors import voice
from .deps import DB, TenantMember, TenantWriter
from .models import Course

router = APIRouter(tags=["voice"])


@router.get("/tenants/{tenant_id}/voice/voices")
def list_voices(tenant_id: uuid.UUID, member: TenantMember, search: str = "") -> dict:
    """The voices in Steady's own ElevenLabs library (Jackson never clones a voice)."""
    if not voice.configured():
        return {"connected": False, "voices": [],
                "note": "set ELEVENLABS_API_KEY to list your voices"}
    try:
        return {"connected": True, "voices": voice.voice_backend().voices(search[:100])}
    except Exception as e:  # noqa: BLE001
        from .connections import scrub
        raise HTTPException(502, scrub(str(e))) from None


class VoiceIn(BaseModel):
    voice_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    purpose: Literal["narration", "ad"] = "narration"
    text: str | None = Field(default=None, max_length=voice.MAX_CHARS)
    title: str | None = Field(default=None, max_length=200)
    course_id: uuid.UUID | None = None
    module: int = Field(default=0, ge=0, le=20)
    lesson: int = Field(default=0, ge=0, le=20)
    business_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/voice", status_code=202)
def request_voice(tenant_id: uuid.UUID, body: VoiceIn, writer: TenantWriter, db: DB) -> dict:
    """Ask for a narration or ad voiceover. Shows the cost now; the run waits for the owner's
    approval of that amount (or runs inside an active Launch's budget)."""
    from .runs import CommandError, create_run
    from .voice import script_for

    biz = body.business_id
    inp = body.model_dump(exclude={"idempotency_key", "business_id"}, mode="json")
    if body.course_id:
        c = db.get(Course, body.course_id)
        if c is None or c.tenant_id != tenant_id:
            raise HTTPException(404, "course not found")
        biz = biz or c.business_id
    try:
        text, about = script_for(db, tenant_id, inp)
    except Exception as e:  # noqa: BLE001 - NonRetryableError with a plain message
        raise HTTPException(422, str(e)) from None
    est = voice.estimate_cents(len(text), voice.model())
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": f"AI voice ({body.purpose}): {about['title'][:150]}",
            "idempotency_key": f"voice-{body.idempotency_key}", "business_id": biz},
            first_task={"kind": "voice_generate", "title": f"AI voice: {about['title'][:150]}",
                        "tool_scope": [], "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id), "characters": len(text), "estimate_cents": est,
            "estimate_usd": f"{est / 100:.2f}", "model": voice.model(),
            "connector": "elevenlabs" if voice.configured() else "mock",
            "how": "Nothing is generated or paid for until you approve this amount (or an "
                   "active Launch approval covers it)."}
