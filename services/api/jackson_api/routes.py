"""Authenticated, tenant-scoped endpoints (phases 03-04).

Command endpoints (POST /commands, runs, approvals) arrive in phase 05.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .deps import DB, CurrentUser, TenantMember, TenantWriter
from .models import AuditEvent, Business, Membership, Tenant

router = APIRouter()


class MembershipOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    role: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    memberships: list[MembershipOut]


class BusinessIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=5000)


class BusinessOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    kind: str
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class AuditEventOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    tenant_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    data: dict
    created_at: datetime


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser, db: DB) -> MeOut:
    rows = db.execute(
        select(Membership, Tenant)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(Membership.user_id == user.id, Membership.status == "active")
        .order_by(Tenant.name)
    ).all()
    return MeOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        memberships=[
            MembershipOut(tenant_id=t.id, tenant_name=t.name, role=m.role) for m, t in rows
        ],
    )


@router.get("/tenants/{tenant_id}/businesses", response_model=list[BusinessOut])
def list_businesses(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[Business]:
    return list(
        db.scalars(
            select(Business).where(Business.tenant_id == tenant_id).order_by(Business.name)
        )
    )


@router.get("/tenants/{tenant_id}/businesses/{business_id}", response_model=BusinessOut)
def get_business(
    tenant_id: uuid.UUID, business_id: uuid.UUID, member: TenantMember, db: DB
) -> Business:
    b = db.scalar(
        select(Business).where(Business.id == business_id, Business.tenant_id == tenant_id)
    )
    if b is None:
        raise HTTPException(404, "business not found")
    return b


@router.post("/tenants/{tenant_id}/businesses", response_model=BusinessOut, status_code=201)
def create_business(
    tenant_id: uuid.UUID, body: BusinessIn, writer: TenantWriter, db: DB
) -> Business:
    b = Business(tenant_id=tenant_id, name=body.name, kind=body.kind, description=body.description)
    db.add(b)
    db.flush()
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_user_id=writer.user_id,
            action="business.created",
            entity_type="Business",
            entity_id=b.id,
            data={"name": b.name, "kind": b.kind},
        )
    )
    # Refresh before commit: the tenant scope (SET LOCAL) ends with the transaction,
    # after which row-level security would hide the row from this session.
    db.refresh(b)
    db.commit()
    return b


@router.get("/tenants/{tenant_id}/audit-events", response_model=list[AuditEventOut])
def list_audit_events(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(200)
        )
    )
