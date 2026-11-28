"""App Factory repos API (ADR-050)."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from .connectors import github_repos as G
from .deps import DB, TenantMember, TenantWriter
from .models import RunEvent

router = APIRouter(tags=["factory"])


class RepoIn(BaseModel):
    site_id: uuid.UUID | None = None
    version_id: uuid.UUID | None = None
    course_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.course_id) == bool(self.site_id and self.version_id):
            raise ValueError("give either course_id, or site_id and version_id")
        return self


@router.post("/tenants/{tenant_id}/factory/repos", status_code=202)
def push_repo(tenant_id: uuid.UUID, body: RepoIn, writer: TenantWriter, db: DB) -> dict:
    """Waits for your approval, then creates (or reuses) a PRIVATE repo and commits."""
    from .runs import CommandError, create_run

    if not G.configured():
        raise HTTPException(409, "set GITHUB_APP_TOKEN and GITHUB_OWNER in Railway")
    inp = body.model_dump(exclude={"idempotency_key"}, mode="json")
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": "Push to a private GitHub repo",
            "idempotency_key": f"repo-{body.idempotency_key}"},
            first_task={"kind": "repo_push", "title": "Push to a private GitHub repo",
                        "tool_scope": [], "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}


@router.get("/tenants/{tenant_id}/factory/repos")
def list_repos(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[dict]:
    rows = db.scalars(select(RunEvent).where(RunEvent.tenant_id == tenant_id,
                                             RunEvent.type == "repo.pushed")
                      .order_by(RunEvent.id.desc()).limit(100)).all()
    return [{**e.data, "at": e.created_at.isoformat()} for e in rows]
