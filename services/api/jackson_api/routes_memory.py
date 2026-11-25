"""Memory API (phase 09): write, recall with citations, history, correct, confirm, export,
delete. `owner_only` memories are visible to the owner role only."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .memory import citation, confirm, correct, delete_memory, history, recall, write_memory
from .models import MemoryRecord
from .runs import CommandError

router = APIRouter()

Kind = Literal["preference", "business_fact", "decision", "project_state", "tool_result",
               "lesson"]
Epistemic = Literal["fact", "inference", "hypothesis"]


class MemoryIn(BaseModel):
    kind: Kind
    epistemic: Epistemic
    content: str = Field(min_length=1, max_length=20_000)
    key: str | None = Field(default=None, max_length=200)
    business_id: uuid.UUID | None = None
    impact: Literal["low", "high"] = "low"
    confidence: float = Field(default=1.0, ge=0, le=1)
    access_scope: Literal["tenant", "owner_only"] = "tenant"
    retain_days: int | None = Field(default=None, ge=1, le=36500)
    source_ref: str | None = Field(default=None, max_length=500)


class MemoryOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    business_id: uuid.UUID | None
    kind: str
    epistemic: str
    key: str | None
    content: str
    impact: str
    confidence: float
    source_type: str
    source_ref: str | None
    source_detail: dict
    access_scope: str
    status: str
    supersedes_id: uuid.UUID | None
    retain_until: datetime | None
    created_at: datetime
    citation: dict = {}


def out(rec: MemoryRecord) -> MemoryOut:
    m = MemoryOut.model_validate(rec)
    m.citation = citation(rec)
    return m


def _err(e: CommandError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": e.message})


def _get(db, tenant_id, memory_id, member) -> MemoryRecord:
    rec = db.scalar(select(MemoryRecord).where(MemoryRecord.id == memory_id,
                                               MemoryRecord.tenant_id == tenant_id))
    if rec is None or (rec.access_scope == "owner_only" and member.role != "owner"):
        raise HTTPException(404, "memory not found")
    return rec


@router.post("/tenants/{tenant_id}/memory", response_model=MemoryOut, status_code=201)
def create(tenant_id: uuid.UUID, body: MemoryIn, writer: TenantWriter, db: DB) -> MemoryOut:
    if body.access_scope == "owner_only" and writer.role != "owner":
        raise HTTPException(403, "only the owner can write owner_only memories")
    try:
        rec = write_memory(db, tenant_id=tenant_id, source_type="owner",
                           user_id=writer.user_id, **body.model_dump())
    except CommandError as e:
        db.rollback()
        raise _err(e) from None
    audit(db, tenant_id, writer.user_id, "memory.written", "MemoryRecord", rec.id,
          status=rec.status, kind=rec.kind)
    db.flush()
    db.refresh(rec)
    o = out(rec)
    db.commit()
    return o


@router.get("/tenants/{tenant_id}/memory", response_model=list[MemoryOut])
def search(tenant_id: uuid.UUID, member: TenantMember, db: DB,
           q: str | None = None, business_id: uuid.UUID | None = None,
           kind: Annotated[list[Kind] | None, Query()] = None,
           limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[MemoryOut]:
    rows = recall(db, tenant_id, q, business_id=business_id, kinds=kind,
                  include_owner_only=member.role == "owner", limit=limit)
    return [out(r) for r in rows]


@router.get("/tenants/{tenant_id}/memory/pending", response_model=list[MemoryOut])
def pending(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[MemoryOut]:
    rows = db.scalars(select(MemoryRecord).where(
        MemoryRecord.tenant_id == tenant_id, MemoryRecord.status == "pending_confirmation"))
    return [out(r) for r in rows if r.access_scope == "tenant" or member.role == "owner"]


@router.get("/tenants/{tenant_id}/memory/export")
def export(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> Response:
    if member.role != "owner":
        raise HTTPException(403, "only the owner can export memory")
    rows = db.scalars(select(MemoryRecord).where(MemoryRecord.tenant_id == tenant_id)
                      .order_by(MemoryRecord.created_at))
    body = "[" + ",".join(out(r).model_dump_json() for r in rows) + "]"
    audit(db, tenant_id, member.user_id, "memory.exported", "Tenant", tenant_id)
    db.commit()
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=memory.json"})


@router.get("/tenants/{tenant_id}/memory/{memory_id}")
def get_one(tenant_id: uuid.UUID, memory_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    rec = _get(db, tenant_id, memory_id, member)
    return {"memory": out(rec).model_dump(mode="json"),
            "history": [out(r).model_dump(mode="json") for r in history(db, rec)]}


class CorrectIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    epistemic: Epistemic | None = None


@router.post("/tenants/{tenant_id}/memory/{memory_id}/correct", response_model=MemoryOut)
def correct_one(tenant_id: uuid.UUID, memory_id: uuid.UUID, body: CorrectIn,
                writer: TenantWriter, db: DB) -> MemoryOut:
    rec = _get(db, tenant_id, memory_id, writer)
    try:
        new = correct(db, rec, writer.user_id, body.content, body.epistemic)
    except CommandError as e:
        db.rollback()
        raise _err(e) from None
    audit(db, tenant_id, writer.user_id, "memory.corrected", "MemoryRecord", new.id,
          correction_of=str(rec.id))
    db.flush()
    db.refresh(new)
    o = out(new)
    db.commit()
    return o


class ConfirmIn(BaseModel):
    accept: bool


@router.post("/tenants/{tenant_id}/memory/{memory_id}/confirm", response_model=MemoryOut)
def confirm_one(tenant_id: uuid.UUID, memory_id: uuid.UUID, body: ConfirmIn,
                writer: TenantWriter, db: DB) -> MemoryOut:
    rec = _get(db, tenant_id, memory_id, writer)
    try:
        confirm(db, rec, body.accept)
    except CommandError as e:
        db.rollback()
        raise _err(e) from None
    audit(db, tenant_id, writer.user_id,
          "memory.confirmed" if body.accept else "memory.rejected", "MemoryRecord", rec.id)
    db.flush()
    db.refresh(rec)
    o = out(rec)
    db.commit()
    return o


@router.delete("/tenants/{tenant_id}/memory/{memory_id}")
def delete_one(tenant_id: uuid.UUID, memory_id: uuid.UUID, member: TenantMember,
               db: DB) -> dict:
    if member.role != "owner":
        raise HTTPException(403, "only the owner can delete memory")
    rec = _get(db, tenant_id, memory_id, member)
    ids = delete_memory(db, rec)
    # Audit keeps ids only, never the deleted content.
    audit(db, tenant_id, member.user_id, "memory.deleted", "MemoryRecord", rec.id,
          versions_deleted=len(ids))
    db.commit()
    return {"deleted": [str(i) for i in ids]}
