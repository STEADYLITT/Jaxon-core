"""Browser sessions (watch + control) and artifact downloads (phase 11)."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from .artifacts import LocalArtifactStore
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import Artifact, BrowserSession

router = APIRouter()


class SessionOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    run_id: uuid.UUID
    task_id: uuid.UUID
    status: str
    control: str
    allowed_domains: list[str]
    current_url: str | None
    title: str | None
    last_action: str | None
    last_screenshot_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None


class ControlIn(BaseModel):
    action: Literal["pause", "takeover", "resume", "terminate"]


def _session(db, tenant_id, session_id) -> BrowserSession:
    s = db.scalar(select(BrowserSession).where(BrowserSession.id == session_id,
                                               BrowserSession.tenant_id == tenant_id))
    if s is None:
        raise HTTPException(404, "browser session not found")
    return s


@router.get("/tenants/{tenant_id}/browser-sessions", response_model=list[SessionOut])
def list_sessions(tenant_id: uuid.UUID, member: TenantMember, db: DB,
                  run_id: uuid.UUID | None = None) -> list[BrowserSession]:
    q = select(BrowserSession).where(BrowserSession.tenant_id == tenant_id)
    if run_id:
        q = q.where(BrowserSession.run_id == run_id)
    return list(db.scalars(q.order_by(BrowserSession.created_at.desc()).limit(100)))


@router.get("/tenants/{tenant_id}/browser-sessions/{session_id}", response_model=SessionOut)
def get_session(tenant_id: uuid.UUID, session_id: uuid.UUID, member: TenantMember,
                db: DB) -> BrowserSession:
    return _session(db, tenant_id, session_id)


@router.post("/tenants/{tenant_id}/browser-sessions/{session_id}/control",
             response_model=SessionOut)
def control(tenant_id: uuid.UUID, session_id: uuid.UUID, body: ControlIn,
            writer: TenantWriter, db: DB) -> SessionOut:
    s = _session(db, tenant_id, session_id)
    if s.status in ("terminated", "closed"):
        raise HTTPException(409, f"session is {s.status}")
    s.control = "none" if body.action == "resume" else body.action
    s.updated_at = func.now()
    audit(db, tenant_id, writer.user_id, f"browser.{body.action}", "BrowserSession", s.id)
    db.flush()
    db.refresh(s)
    out = SessionOut.model_validate(s)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/artifacts/{artifact_id}")
def get_artifact(tenant_id: uuid.UUID, artifact_id: uuid.UUID, member: TenantMember,
                 db: DB) -> Response:
    a = db.scalar(select(Artifact).where(Artifact.id == artifact_id,
                                         Artifact.tenant_id == tenant_id))
    if a is None:
        raise HTTPException(404, "artifact not found")
    data = LocalArtifactStore().get(a.storage_key)
    return Response(data, media_type=a.content_type,
                    headers={"X-Artifact-Sha256": a.sha256, "Cache-Control": "private"})


@router.get("/tenants/{tenant_id}/artifacts/{artifact_id}/meta")
def artifact_meta(tenant_id: uuid.UUID, artifact_id: uuid.UUID, member: TenantMember,
                  db: DB) -> dict:
    a = db.scalar(select(Artifact).where(Artifact.id == artifact_id,
                                         Artifact.tenant_id == tenant_id))
    if a is None:
        raise HTTPException(404, "artifact not found")
    return {"id": str(a.id), "kind": a.kind, "content_type": a.content_type,
            "size_bytes": a.size_bytes, "sha256": a.sha256, "meta": a.meta,
            "run_id": str(a.run_id) if a.run_id else None, "created_at": a.created_at}
