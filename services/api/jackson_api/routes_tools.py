"""Tool registry, owner grants and the per-run tool call log (phase 10)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import tools
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import Business, ToolCall, ToolGrant

router = APIRouter()


class GrantIn(BaseModel):
    tool_name: str = Field(min_length=1, max_length=100)
    business_id: uuid.UUID | None = None
    config: dict = Field(default_factory=dict)


class GrantOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    tool_name: str
    business_id: uuid.UUID | None
    status: str
    config: dict
    created_at: datetime


class ToolCallOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    task_id: uuid.UUID
    tool_name: str
    mode: str
    input: dict
    output: dict | None
    status: str
    error: str | None
    duration_ms: int
    created_at: datetime


@router.get("/tenants/{tenant_id}/tools")
def list_tools(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[dict]:
    granted = {g.tool_name for g in db.scalars(select(ToolGrant).where(
        ToolGrant.tenant_id == tenant_id, ToolGrant.status == "active"))}
    return [{**t.describe(), "granted": t.name in granted} for t in tools.all_tools()]


@router.post("/tenants/{tenant_id}/tool-grants", response_model=GrantOut, status_code=201)
def grant(tenant_id: uuid.UUID, body: GrantIn, writer: TenantWriter, db: DB) -> GrantOut:
    if writer.role != "owner":
        raise HTTPException(403, "only the owner can grant tools")
    if tools.get(body.tool_name) is None:
        raise HTTPException(422, f"unknown tool '{body.tool_name}'")
    if body.business_id and db.scalar(select(Business.id).where(
            Business.id == body.business_id, Business.tenant_id == tenant_id)) is None:
        raise HTTPException(422, "business not found in this tenant")
    g = ToolGrant(tenant_id=tenant_id, business_id=body.business_id, tool_name=body.tool_name,
                  config=body.config, granted_by=writer.user_id)
    db.add(g)
    db.flush()
    audit(db, tenant_id, writer.user_id, "tool.granted", "ToolGrant", g.id,
          tool=body.tool_name, config=body.config)
    db.refresh(g)
    out = GrantOut.model_validate(g)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/tool-grants", response_model=list[GrantOut])
def list_grants(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[ToolGrant]:
    return list(db.scalars(select(ToolGrant).where(ToolGrant.tenant_id == tenant_id)
                           .order_by(ToolGrant.created_at)))


@router.post("/tenants/{tenant_id}/tool-grants/{grant_id}/revoke", response_model=GrantOut)
def revoke(tenant_id: uuid.UUID, grant_id: uuid.UUID, writer: TenantWriter, db: DB) -> GrantOut:
    if writer.role != "owner":
        raise HTTPException(403, "only the owner can revoke tools")
    g = db.scalar(select(ToolGrant).where(ToolGrant.id == grant_id,
                                          ToolGrant.tenant_id == tenant_id))
    if g is None:
        raise HTTPException(404, "grant not found")
    g.status = "revoked"
    audit(db, tenant_id, writer.user_id, "tool.revoked", "ToolGrant", g.id, tool=g.tool_name)
    db.flush()
    db.refresh(g)
    out = GrantOut.model_validate(g)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/runs/{run_id}/tool-calls", response_model=list[ToolCallOut])
def run_tool_calls(tenant_id: uuid.UUID, run_id: uuid.UUID, member: TenantMember,
                   db: DB) -> list[ToolCall]:
    return list(db.scalars(select(ToolCall).where(ToolCall.tenant_id == tenant_id,
                                                  ToolCall.run_id == run_id)
                           .order_by(ToolCall.created_at)))
