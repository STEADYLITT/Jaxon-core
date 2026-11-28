"""Amazon/eBay product research API (ADR-053)."""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .connectors import marketplaces as M
from .deps import DB, TenantWriter

router = APIRouter(tags=["ecommerce"])


class ResearchIn(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=5)
    landed_cost_cents: int | None = Field(default=None, ge=0, le=10_000_000)
    amazon: bool = True
    ebay: bool = True
    business_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/ecommerce/research", status_code=202)
def research(tenant_id: uuid.UUID, body: ResearchIn, writer: TenantWriter, db: DB) -> dict:
    from .runs import CommandError, create_run

    if not ((body.amazon and M.amazon_configured()) or (body.ebay and M.ebay_configured())):
        raise HTTPException(409, "connect Amazon (AMAZON_SP_CLIENT_ID, AMAZON_SP_CLIENT_SECRET, "
                                 "AMAZON_SP_REFRESH_TOKEN) or eBay (EBAY_CLIENT_ID, "
                                 "EBAY_CLIENT_SECRET) in Railway")
    inp = body.model_dump(exclude={"idempotency_key", "business_id"})
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": f"Product research: {', '.join(body.keywords)}"[:300],
            "idempotency_key": f"mkt-{body.idempotency_key}", "business_id": body.business_id},
            first_task={"kind": "marketplace_research", "title": "Amazon/eBay product research",
                        "tool_scope": [], "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}
