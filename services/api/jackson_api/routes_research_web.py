"""Web research API (ADR-043): ask for Google search, Trends or Maps places research via
Apify. Shows the cost cap now; the run waits for approval (or a Launch's budget)."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .connectors import webresearch as R
from .deps import DB, TenantWriter

router = APIRouter(tags=["research"])


class WebIn(BaseModel):
    kind: Literal["search", "trends", "places"]
    terms: list[str] = Field(min_length=1, max_length=10)
    location: str = Field(default="", max_length=200)
    country: str = Field(default="us", pattern=r"^[A-Za-z]{2}$")
    items: int = Field(default=20, ge=1, le=R.MAX_ITEMS)
    business_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/research/web", status_code=202)
def web_research(tenant_id: uuid.UUID, body: WebIn, writer: TenantWriter, db: DB) -> dict:
    from .runs import CommandError, create_run

    if not R.configured():
        raise HTTPException(409, "web research isn't connected: set APIFY_TOKEN in Railway")
    try:
        R.build_input(body.kind, body.terms, body.location, body.items, body.country)
    except Exception as e:  # noqa: BLE001 - plain validation message
        raise HTTPException(422, str(e)) from None
    cap = R.estimate_cents(body.kind, body.items)
    inp = body.model_dump(exclude={"idempotency_key", "business_id"})
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": f"Web research ({body.kind}): "
                                          f"{', '.join(body.terms)}"[:300],
            "idempotency_key": f"web-{body.idempotency_key}", "business_id": body.business_id},
            first_task={"kind": "web_research", "title": f"Web research ({body.kind})",
                        "tool_scope": [], "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id), "cap_cents": cap, "cap_usd": f"{cap / 100:.2f}",
            "actor": R.ACTORS[body.kind][0],
            "how": "Nothing runs or is paid for until you approve this cap (or an active "
                   "Launch approval covers it). Apify can never charge more than the cap."}
