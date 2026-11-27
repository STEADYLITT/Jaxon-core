"""Course Factory API (ADR-040): list courses, read one, start a build, review its pages."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .deps import DB, TenantMember, TenantWriter
from .models import Course, Launch

router = APIRouter(tags=["courses"])


def _course(db, tenant_id: uuid.UUID, cid: uuid.UUID) -> Course:
    c = db.get(Course, cid)
    if c is None or c.tenant_id != tenant_id:
        raise HTTPException(404, "course not found")
    return c


def _summary(c: Course, launch: Launch | None) -> dict:
    lessons = sum(len(m["lessons"]) for m in (c.outline or {}).get("modules", []))
    return {"id": str(c.id), "title": c.title, "status": c.status, "mode": c.mode,
            "business_id": str(c.business_id), "run_id": str(c.run_id) if c.run_id else None,
            "modules": len((c.outline or {}).get("modules", [])), "lessons": lessons,
            "price": c.price, "created_at": c.created_at.isoformat(),
            "launch": {"id": str(launch.id), "status": launch.status} if launch else None,
            "platform_passed": (c.checks or {}).get("platform", {}).get("passed"),
            "sales_passed": (c.checks or {}).get("sales", {}).get("passed")}


@router.get("/tenants/{tenant_id}/courses")
def list_courses(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[dict]:
    rows = db.scalars(select(Course).where(Course.tenant_id == tenant_id)
                      .order_by(Course.created_at.desc()).limit(100)).all()
    launches = {la.id: la for la in db.scalars(select(Launch).where(
        Launch.id.in_([c.launch_id for c in rows if c.launch_id])))}
    return [_summary(c, launches.get(c.launch_id)) for c in rows]


@router.get("/tenants/{tenant_id}/courses/{cid}")
def get_course(tenant_id: uuid.UUID, cid: uuid.UUID, member: TenantMember, db: DB) -> dict:
    c = _course(db, tenant_id, cid)
    la = db.get(Launch, c.launch_id) if c.launch_id else None
    return {**_summary(c, la), "brief": c.brief, "outline": c.outline, "lessons": c.lessons,
            "checks": c.checks, "platform_pages": sorted(c.platform_files),
            "sales_pages": sorted(c.sales_files),
            "launch_plan": la.plan if la else None}


@router.get("/tenants/{tenant_id}/courses/{cid}/files/{which}/{path}")
def course_file(tenant_id: uuid.UUID, cid: uuid.UUID, which: Literal["platform", "sales"],
                path: str, member: TenantMember, db: DB) -> Response:
    """One draft page for review. Strict CSP: no scripts run, forms submit nowhere."""
    c = _course(db, tenant_id, cid)
    files = c.platform_files if which == "platform" else c.sales_files
    if path not in files:
        raise HTTPException(404, "file not found")
    return Response(files[path], media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                   "img-src data:; base-uri 'none'; form-action 'none'",
        "X-Content-Type-Options": "nosniff"})


class BuildIn(BaseModel):
    brief: str = Field(min_length=3, max_length=2000)
    business_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/courses", status_code=202)
def build_course(tenant_id: uuid.UUID, body: BuildIn, writer: TenantWriter, db: DB) -> dict:
    """Start a course build directly (the command bar does the same through routing)."""
    from .runs import CommandError, create_run

    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": f"Build course: {body.brief}"[:2000],
            "idempotency_key": body.idempotency_key, "business_id": body.business_id},
            first_task={"kind": "course_build", "title": f"Build course: {body.brief}"[:200],
                        "tool_scope": [], "input": {"brief": body.brief}})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}
