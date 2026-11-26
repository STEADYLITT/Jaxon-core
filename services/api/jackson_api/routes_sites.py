"""Website factory API (phase 17): sites, builds, previews, launch and rollback."""

import re
import secrets
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import SiteProject, SiteVersion
from .runs import CommandError, create_run
from .sites import DOMAIN_RE, txt_record

router = APIRouter()
HEX = r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"


class Service(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class Brand(BaseModel):
    primary: str = Field(default="#1f4e79", pattern=HEX)
    text: str = Field(default="#1a1a1a", pattern=HEX)
    background: str = Field(default="#ffffff", pattern=HEX)


class Requirements(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    tagline: str = Field(min_length=3, max_length=200)
    intro: str = Field(default="", max_length=5000)
    about: str = Field(default="", max_length=10_000)
    services: list[Service] = Field(default_factory=list, max_length=20)
    contact_email: str | None = Field(default=None, pattern=r"^[^@\s<>\"']+@[^@\s<>\"']+\.\w+$")
    pages: list[Literal["about", "services", "contact"]] = ["about", "services", "contact"]
    brand: Brand = Field(default_factory=Brand)
    language: str = Field(default="en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")


class SiteIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    business_id: uuid.UUID | None = None
    requirements: Requirements


class VersionOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    number: int
    passed: bool
    checks: dict
    run_id: uuid.UUID | None
    created_at: datetime


class SiteOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    name: str
    business_id: uuid.UUID | None
    requirements: dict
    status: str
    domain: str | None
    live_version_id: uuid.UUID | None
    previous_version_id: uuid.UUID | None
    live_url: str | None
    launched_at: datetime | None
    created_at: datetime
    versions: list[VersionOut] = []


def _site(db, tenant_id, sid) -> SiteProject:
    s = db.get(SiteProject, sid)
    if s is None or s.tenant_id != tenant_id:
        raise HTTPException(404, "site not found")
    return s


def _out(db, s: SiteProject) -> SiteOut:
    vs = db.scalars(select(SiteVersion).where(SiteVersion.site_id == s.id)
                    .order_by(SiteVersion.number.desc())).all()
    out = SiteOut.model_validate(s)
    out.versions = [VersionOut.model_validate(v) for v in vs]
    return out


def _run(db, tenant_id, user_id, s, objective, key, task) -> uuid.UUID:
    try:
        run, _ = create_run(db, tenant_id, user_id, {
            "type": "EXECUTE" if s.business_id else "BUILD", "objective": objective,
            "idempotency_key": key, "business_id": s.business_id}, first_task=task)
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    return run.id


@router.post("/tenants/{tenant_id}/sites", status_code=201, response_model=SiteOut)
def create_site(tenant_id: uuid.UUID, body: SiteIn, writer: TenantWriter, db: DB):
    s = SiteProject(tenant_id=tenant_id, business_id=body.business_id, name=body.name,
                    requirements=body.requirements.model_dump(), status="draft")
    db.add(s)
    db.flush()
    audit(db, tenant_id, writer.user_id, "site.created", "SiteProject", s.id)
    out = _out(db, s)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/sites", response_model=list[SiteOut])
def list_sites(tenant_id: uuid.UUID, member: TenantMember, db: DB):
    rows = db.scalars(select(SiteProject).where(SiteProject.tenant_id == tenant_id)
                      .order_by(SiteProject.created_at.desc())).all()
    return [_out(db, s) for s in rows]


@router.get("/tenants/{tenant_id}/sites/{sid}", response_model=SiteOut)
def get_site(tenant_id: uuid.UUID, sid: uuid.UUID, member: TenantMember, db: DB):
    return _out(db, _site(db, tenant_id, sid))


@router.put("/tenants/{tenant_id}/sites/{sid}/requirements", response_model=SiteOut)
def update_requirements(tenant_id: uuid.UUID, sid: uuid.UUID, body: Requirements,
                        writer: TenantWriter, db: DB):
    """New requirements only affect future builds; the live version is untouched."""
    s = _site(db, tenant_id, sid)
    s.requirements = body.model_dump()
    out = _out(db, s)
    db.commit()
    return out


class KeyIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/sites/{sid}/build", status_code=202)
def build(tenant_id: uuid.UUID, sid: uuid.UUID, body: KeyIn, writer: TenantWriter, db: DB):
    s = _site(db, tenant_id, sid)
    run_id = _run(db, tenant_id, writer.user_id, s, f"Build website: {s.name}",
                  body.idempotency_key, {"kind": "site_build", "title": "Build and test site",
                                         "tool_scope": [], "input": {"site_id": str(s.id)}})
    db.commit()
    return {"run_id": str(run_id)}


@router.get("/tenants/{tenant_id}/sites/{sid}/versions/{vid}/files/{path}")
def preview_file(tenant_id: uuid.UUID, sid: uuid.UUID, vid: uuid.UUID, path: str,
                 member: TenantMember, db: DB) -> Response:
    """Serve one built page for review. Strict CSP: no scripts can run."""
    _site(db, tenant_id, sid)
    v = db.get(SiteVersion, vid)
    if v is None or v.site_id != sid or path not in v.files:
        raise HTTPException(404, "file not found")
    return Response(v.files[path], media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                   "img-src data:; base-uri 'none'; form-action 'none'",
        "X-Content-Type-Options": "nosniff"})


class LaunchIn(BaseModel):
    version_id: uuid.UUID
    domain: str = Field(min_length=4, max_length=253)
    idempotency_key: str = Field(min_length=8, max_length=200)

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        v = v.strip().lower().rstrip(".")
        if not DOMAIN_RE.fullmatch(v) or re.search(r"(^|\.)(localhost|local|internal)$", v):
            raise ValueError("not a public domain name")
        return v


@router.post("/tenants/{tenant_id}/sites/{sid}/launch", status_code=202)
def launch(tenant_id: uuid.UUID, sid: uuid.UUID, body: LaunchIn, writer: TenantWriter,
           db: DB):
    """Start a launch. The worker verifies the domain, then waits for the owner's
    approval (production_change) before anything goes live."""
    s = _site(db, tenant_id, sid)
    v = db.get(SiteVersion, body.version_id)
    if v is None or v.site_id != s.id:
        raise HTTPException(404, "version not found")
    if not v.passed:
        raise HTTPException(409, {"code": "version_not_tested",
                                  "problems": v.checks.get("static", {}).get("problems", [])
                                  + v.checks.get("preview", {}).get("problems", [])})
    if s.domain != body.domain or not s.domain_token:
        s.domain, s.domain_token = body.domain, secrets.token_hex(16)
    run_id = _run(db, tenant_id, writer.user_id, s,
                  f"Launch {s.name} v{v.number} on {body.domain}", body.idempotency_key,
                  {"kind": "site_launch", "title": f"Launch v{v.number}", "tool_scope": [],
                   "input": {"site_id": str(s.id), "version_id": str(v.id)}})
    audit(db, tenant_id, writer.user_id, "site.launch_requested", "SiteProject", s.id,
          version=v.number, domain=body.domain)
    record = txt_record(s.domain, s.domain_token)
    db.commit()
    return {"run_id": str(run_id), "dns_record": record,
            "next": "add the DNS record, then approve the launch in your approvals inbox"}


@router.post("/tenants/{tenant_id}/sites/{sid}/rollback", response_model=SiteOut)
def rollback(tenant_id: uuid.UUID, sid: uuid.UUID, writer: TenantWriter, db: DB):
    """Put the previous live version back (it already passed its tests and was approved).
    MOCK hosting: only the record changes until a hosting account is connected."""
    s = _site(db, tenant_id, sid)
    if s.status != "live" or s.previous_version_id is None:
        raise HTTPException(409, "nothing to roll back to")
    s.live_version_id, s.previous_version_id = s.previous_version_id, s.live_version_id
    v = db.get(SiteVersion, s.live_version_id)
    s.live_url = f"mock://sites/{s.domain}/v{v.number}/"
    audit(db, tenant_id, writer.user_id, "site.rolled_back", "SiteProject", s.id,
          to_version=v.number)
    out = _out(db, s)
    db.commit()
    return out
