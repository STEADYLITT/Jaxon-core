"""Real-estate research API (ADR-046): comps and skip tracing, research only."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .connectors import realestate as RE
from .deps import DB, TenantWriter

router = APIRouter(tags=["realestate"])


class Address(BaseModel):
    street: str = Field(min_length=3, max_length=120)
    city: str = Field(default="", max_length=120)
    state: str = Field(default="", max_length=2)
    zip: str = Field(default="", max_length=10)


class CompsIn(BaseModel):
    address: Address
    miles: float = Field(default=1.0, ge=0.25, le=5.0)
    take: int = Field(default=10, ge=1, le=RE.MAX_COMPS)
    business_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


class TraceIn(BaseModel):
    address: Address
    business_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


def _start(db, tenant_id, writer, kind: str, title: str, body) -> dict:
    from .runs import CommandError, create_run

    if not RE.configured():
        raise HTTPException(409, "set BATCHDATA_API_TOKEN and BATCHDATA_CENTS_PER_RECORD in "
                                 "Railway")
    try:
        RE.address(body.address.model_dump())
        per = RE.cents_per_record()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, str(e)) from None
    inp = body.model_dump(exclude={"idempotency_key", "business_id"})
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": title[:300],
            "idempotency_key": f"{kind}-{body.idempotency_key}",
            "business_id": body.business_id},
            first_task={"kind": kind, "title": title[:200], "tool_scope": [], "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    n = getattr(body, "take", 1)
    return {"run_id": str(run.id), "estimate_cents": per * n,
            "how": "Nothing is looked up or paid for until you approve it."}


@router.post("/tenants/{tenant_id}/realestate/comps", status_code=202)
def comps(tenant_id: uuid.UUID, body: CompsIn, writer: TenantWriter, db: DB) -> dict:
    return _start(db, tenant_id, writer, "property_comps",
                  f"Comps: {body.address.street}, {body.address.city}", body)


@router.post("/tenants/{tenant_id}/realestate/skip-trace", status_code=202)
def skip_trace(tenant_id: uuid.UUID, body: TraceIn, writer: TenantWriter, db: DB) -> dict:
    return _start(db, tenant_id, writer, "skip_trace",
                  f"Skip trace: {body.address.street}, {body.address.city}", body)
