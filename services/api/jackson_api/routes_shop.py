"""Shop products: list, build a draft store from an opportunity, request publishing."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .deps import DB, TenantMember, TenantWriter
from .models import Opportunity, ShopProduct
from .runs import CommandError, create_run

router = APIRouter()


class ShopProductOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    backend: str
    store: str | None
    external_id: str
    handle: str
    title: str
    price_cents: int
    status: str
    verified: bool
    verified_at: datetime | None
    admin_url: str | None
    opportunity_id: uuid.UUID | None
    run_id: uuid.UUID | None
    created_at: datetime


class KeyIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


def _start(db, tenant_id, user_id, objective, key, business_id, first_task):
    try:
        run, created = create_run(db, tenant_id, user_id, {
            "type": "EXECUTE" if business_id else "BUILD", "objective": objective,
            "idempotency_key": key, "business_id": business_id}, first_task=first_task)
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id), "created": created}


@router.get("/tenants/{tenant_id}/shop/products", response_model=list[ShopProductOut])
def list_products(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[ShopProduct]:
    return list(db.scalars(select(ShopProduct).where(ShopProduct.tenant_id == tenant_id)
                           .order_by(ShopProduct.created_at.desc())))


@router.post("/tenants/{tenant_id}/opportunities/{opp_id}/build-store", status_code=202)
def build_store(tenant_id: uuid.UUID, opp_id: uuid.UUID, body: KeyIn, writer: TenantWriter,
                db: DB) -> dict:
    opp = db.scalar(select(Opportunity).where(Opportunity.id == opp_id,
                                              Opportunity.tenant_id == tenant_id))
    if opp is None:
        raise HTTPException(404, "opportunity not found")
    return _start(db, tenant_id, writer.user_id, f"Build draft store for: {opp.title}",
                  body.idempotency_key, opp.business_id,
                  {"kind": "store_build", "title": f"Draft store: {opp.title[:250]}",
                   "input": {"opportunity_id": str(opp.id)}})


@router.post("/tenants/{tenant_id}/shop/products/{product_id}/publish", status_code=202)
def publish(tenant_id: uuid.UUID, product_id: uuid.UUID, body: KeyIn, writer: TenantWriter,
            db: DB) -> dict:
    """Queues publishing. The worker will stop and ask for approval (production change)."""
    p = db.scalar(select(ShopProduct).where(ShopProduct.id == product_id,
                                            ShopProduct.tenant_id == tenant_id))
    if p is None:
        raise HTTPException(404, "product not found")
    if not p.verified:
        raise HTTPException(409, "only verified drafts can be published")
    return _start(db, tenant_id, writer.user_id, f"Publish {p.title}", body.idempotency_key,
                  p.business_id, {"kind": "shopify_publish", "title": f"Publish: {p.title}",
                                  "input": {"shop_product_id": str(p.id)}})
