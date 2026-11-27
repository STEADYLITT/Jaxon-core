"""Personas, content drafts, review, approval and scheduling (phase 16)."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .content import review
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import CHANNELS, Approval, ContentItem, Persona, Task
from .policy import Action, Category
from .runs import CommandError, create_run

router = APIRouter()
Channel = Literal["instagram", "tiktok", "youtube_shorts", "x", "facebook", "email", "blog"]
assert set(Channel.__args__) == set(CHANNELS)


class PersonaIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    kind: Literal["virtual_influencer", "brand_voice"]
    fictional: bool = True
    bio: str = ""
    brand_bible: dict = Field(default_factory=dict)
    disclosure: str = Field(default="AI-generated character", min_length=3, max_length=300)
    do_not_impersonate: list[str] = Field(default_factory=list)
    business_id: uuid.UUID | None = None


class ContentIn(BaseModel):
    channel: Channel
    kind: str = "post"
    title: str = ""
    body: str = Field(min_length=1, max_length=200_000)
    hashtags: list[str] = Field(default_factory=list)
    sponsored: bool = False
    assets: list[dict] = Field(default_factory=list)
    persona_id: uuid.UUID | None = None
    business_id: uuid.UUID | None = None


class ContentOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    persona_id: uuid.UUID | None
    channel: str
    kind: str
    title: str
    body: str
    hashtags: list[str]
    sponsored: bool
    assets: list[dict]
    status: str
    review: dict
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    scheduled_for: datetime | None
    external_post_id: str | None
    created_at: datetime


def _persona_dict(p: Persona | None) -> dict | None:
    if p is None:
        return None
    return {"id": str(p.id), "name": p.name, "kind": p.kind, "fictional": p.fictional,
            "brand_bible": p.brand_bible, "disclosure": p.disclosure,
            "do_not_impersonate": p.do_not_impersonate}


def _item(db, tenant_id, cid) -> ContentItem:
    c = db.scalar(select(ContentItem).where(ContentItem.id == cid,
                                            ContentItem.tenant_id == tenant_id))
    if c is None:
        raise HTTPException(404, "content not found")
    return c


def _review(db, c: ContentItem) -> dict:
    p = db.get(Persona, c.persona_id) if c.persona_id else None
    return review({"channel": c.channel, "title": c.title, "body": c.body,
                   "hashtags": c.hashtags, "sponsored": c.sponsored, "assets": c.assets},
                  _persona_dict(p))


def _out(db, c) -> ContentOut:
    db.flush()
    db.refresh(c)
    return ContentOut.model_validate(c)


@router.post("/tenants/{tenant_id}/personas", status_code=201)
def create_persona(tenant_id: uuid.UUID, body: PersonaIn, writer: TenantWriter, db: DB) -> dict:
    if body.kind == "virtual_influencer" and not body.fictional:
        raise HTTPException(422, "virtual influencers must be original fictional personas "
                                 "(no impersonation of real people)")
    p = Persona(tenant_id=tenant_id, **body.model_dump())
    db.add(p)
    db.flush()
    audit(db, tenant_id, writer.user_id, "persona.created", "Persona", p.id, name=p.name)
    out = {"id": str(p.id), **body.model_dump(mode="json")}
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/personas")
def list_personas(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[dict]:
    return [{"id": str(p.id), "name": p.name, "kind": p.kind, "disclosure": p.disclosure,
             "status": p.status} for p in db.scalars(select(Persona).where(
                 Persona.tenant_id == tenant_id).order_by(Persona.name))]


@router.post("/tenants/{tenant_id}/content", response_model=ContentOut, status_code=201)
def create_content(tenant_id: uuid.UUID, body: ContentIn, writer: TenantWriter, db: DB):
    c = ContentItem(tenant_id=tenant_id, status="draft", review={}, **body.model_dump())
    db.add(c)
    db.flush()
    rv = _review(db, c)
    c.review, c.status = rv, "in_review" if rv["passed"] else "draft"
    out = _out(db, c)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/content", response_model=list[ContentOut])
def calendar(tenant_id: uuid.UUID, member: TenantMember, db: DB, status: str | None = None,
             persona_id: uuid.UUID | None = None):
    q = select(ContentItem).where(ContentItem.tenant_id == tenant_id)
    if status:
        q = q.where(ContentItem.status == status)
    if persona_id:
        q = q.where(ContentItem.persona_id == persona_id)
    return list(db.scalars(q.order_by(ContentItem.scheduled_for.asc().nulls_last(),
                                      ContentItem.created_at.desc()).limit(500)))


@router.get("/tenants/{tenant_id}/content/{cid}", response_model=ContentOut)
def get_content(tenant_id: uuid.UUID, cid: uuid.UUID, member: TenantMember, db: DB):
    return _item(db, tenant_id, cid)


class EditIn(BaseModel):
    title: str | None = None
    body: str | None = None
    hashtags: list[str] | None = None
    assets: list[dict] | None = None
    sponsored: bool | None = None


@router.patch("/tenants/{tenant_id}/content/{cid}", response_model=ContentOut)
def edit(tenant_id: uuid.UUID, cid: uuid.UUID, body: EditIn, writer: TenantWriter, db: DB):
    c = _item(db, tenant_id, cid)
    if c.status in ("scheduled", "published"):
        raise HTTPException(409, f"content is {c.status}; it can't be edited")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(c, k, v)
    rv = _review(db, c)  # any edit clears approval and re-reviews
    c.review, c.approved_by, c.approved_at = rv, None, None
    c.status = "in_review" if rv["passed"] else "draft"
    out = _out(db, c)
    db.commit()
    return out


@router.post("/tenants/{tenant_id}/content/{cid}/approve", response_model=ContentOut)
def approve(tenant_id: uuid.UUID, cid: uuid.UUID, writer: TenantWriter, db: DB):
    c = _item(db, tenant_id, cid)
    rv = _review(db, c)
    if not rv["passed"]:
        c.review = rv
        db.commit()
        raise HTTPException(409, {"code": "review_failed", "problems": rv["problems"]})
    if c.status not in ("in_review", "draft"):
        raise HTTPException(409, f"content is {c.status}")
    c.review, c.status = rv, "approved"
    c.approved_by, c.approved_at = writer.user_id, func.now()
    audit(db, tenant_id, writer.user_id, "content.approved", "ContentItem", c.id)
    out = _out(db, c)
    db.commit()
    return out


@router.post("/tenants/{tenant_id}/content/{cid}/reject", response_model=ContentOut)
def reject(tenant_id: uuid.UUID, cid: uuid.UUID, writer: TenantWriter, db: DB):
    """Reject a draft, or cancel a scheduled post before it goes out (the publish task then
    finds the content unapproved and stops without posting)."""
    c = _item(db, tenant_id, cid)
    if c.status == "published":
        raise HTTPException(409, "content is already published")
    c.status, c.approved_by, c.approved_at = "rejected", None, None
    audit(db, tenant_id, writer.user_id, "content.rejected", "ContentItem", c.id)
    out = _out(db, c)
    db.commit()
    return out


class ScheduleIn(BaseModel):
    scheduled_for: datetime
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/content/{cid}/schedule", status_code=202)
def schedule(tenant_id: uuid.UUID, cid: uuid.UUID, body: ScheduleIn, writer: TenantWriter,
             db: DB) -> dict:
    """The owner's schedule click IS the posting approval, recorded in the approvals table
    for exactly this publish task. The worker posts at the scheduled time."""
    c = _item(db, tenant_id, cid)
    if c.status != "approved" or c.approved_by is None:
        raise HTTPException(409, "only approved content can be scheduled")
    when = body.scheduled_for if body.scheduled_for.tzinfo else \
        body.scheduled_for.replace(tzinfo=UTC)
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "EXECUTE" if c.business_id else "BUILD",
            "objective": f"Publish {c.channel} {c.kind}: {c.title[:150]}",
            "idempotency_key": body.idempotency_key, "business_id": c.business_id},
            first_task={"kind": "content_publish", "title": f"Publish {c.channel} post",
                        "input": {"content_id": str(c.id)}, "next_attempt_at": when,
                        "tool_scope": []})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    task = db.scalar(select(Task).where(Task.run_id == run.id))
    action = Action(Category.PUBLIC_POST, f"publish content {c.id}")
    db.add(Approval(tenant_id=tenant_id, run_id=run.id, task_id=task.id,
                    category=action.category.value, description=action.description,
                    amount_cents=0, fingerprint=action.fingerprint(task.id),
                    reason="owner scheduled this approved content", status="approved",
                    decided_by=writer.user_id, decided_at=func.now(),
                    decision_note="approved by scheduling"))
    c.status, c.scheduled_for = "scheduled", when
    audit(db, tenant_id, writer.user_id, "content.scheduled", "ContentItem", c.id,
          scheduled_for=when.isoformat())
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id), "scheduled_for": when.isoformat()}


class GenerateIn(BaseModel):
    channel: Channel = "instagram"
    brief: str = Field(min_length=5, max_length=5000)
    persona_id: uuid.UUID | None = None
    count: int = Field(default=3, ge=1, le=10)
    sponsored: bool = False
    business_id: uuid.UUID | None = None


@router.post("/tenants/{tenant_id}/content/generate", status_code=202)
def generate(tenant_id: uuid.UUID, body: GenerateIn, writer: TenantWriter, db: DB) -> dict:
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": f"Content: {body.brief[:200]}",
            "idempotency_key": f"content-{uuid.uuid4().hex}", "business_id": body.business_id},
            first_task={"kind": "content_generate", "title": "Draft content", "tool_scope": [],
                        "input": body.model_dump(mode="json")})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id)}


# --- UGC video clips (ADR-036) ---------------------------------------------------------------

class VideoIn(BaseModel):
    prompt: str = Field(default="", max_length=4000)
    duration_s: Literal[4, 6, 8] = 8
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    resolution: Literal["720p", "1080p", "4k"] = "1080p"
    audio: bool = True
    idempotency_key: str = Field(min_length=8, max_length=200)


def _estimate(duration_s: int, resolution: str, audio: bool) -> dict:
    from .connectors import video
    from .worker_errors import NonRetryableError

    try:
        cents = video.estimate_cents(duration_s, resolution, audio)
    except NonRetryableError as e:
        raise HTTPException(422, str(e)) from None
    live = video.configured()
    return {"estimate_cents": cents, "estimate_usd": f"{cents / 100:.2f}",
            "model": video.model() if live else "mock", "connector": "fal" if live else "mock",
            "note": "fal.ai charges per second of video; nothing is generated until you "
                    "approve this amount (or an active Launch approval covers it)."}


@router.get("/tenants/{tenant_id}/video/estimate")
def video_estimate(tenant_id: uuid.UUID, member: TenantMember, duration_s: int = 8,
                   resolution: Literal["720p", "1080p", "4k"] = "1080p",
                   audio: bool = True) -> dict:
    return _estimate(duration_s, resolution, audio)


@router.post("/tenants/{tenant_id}/content/{cid}/video", status_code=202)
def request_video(tenant_id: uuid.UUID, cid: uuid.UUID, body: VideoIn, writer: TenantWriter,
                  db: DB) -> dict:
    """Ask for a clip. Shows the cost now; the run waits for the owner's approval of that
    amount in the inbox (or runs inside an active Launch approval's budget)."""
    est = _estimate(body.duration_s, body.resolution, body.audio)
    c = db.get(ContentItem, cid)
    if c is None or c.tenant_id != tenant_id:
        raise HTTPException(404, "content not found")
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": f"UGC video: {c.title[:150]}",
            "idempotency_key": f"video-{body.idempotency_key}", "business_id": c.business_id},
            first_task={"kind": "content_video", "title": "Generate video clip",
                        "tool_scope": [], "input": {"content_id": str(cid),
                                                    **body.model_dump(exclude={"idempotency_key"})}},
            launch_id=c.launch_id)
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id), **est}
