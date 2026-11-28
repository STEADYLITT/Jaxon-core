"""APEX-7 paper trading API (ADR-047): snapshot the paper account, place PAPER orders."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .connectors import broker as B
from .deps import DB, TenantWriter

router = APIRouter(tags=["trading"])


def _start(db, tenant_id, writer, kind, title, inp, key):
    from .runs import CommandError, create_run

    if not B.configured():
        raise HTTPException(409, "set ALPACA_PAPER_KEY_ID and ALPACA_PAPER_SECRET_KEY "
                                 "(paper account keys) in Railway")
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "SIMULATE", "objective": title[:300], "idempotency_key": f"{kind}-{key}"},
            first_task={"kind": kind, "title": title[:200], "tool_scope": [], "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id), "paper": True}


class SnapIn(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["MNQ"], max_length=5)
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/trading/paper/snapshot", status_code=202)
def snapshot(tenant_id: uuid.UUID, body: SnapIn, writer: TenantWriter, db: DB) -> dict:
    return _start(db, tenant_id, writer, "paper_snapshot", "APEX-7 paper account snapshot",
                  {"symbols": body.symbols}, body.idempotency_key)


class OrderIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    qty: int = Field(ge=1, le=B.MAX_PAPER_QTY)
    side: Literal["buy", "sell"]
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/trading/paper/orders", status_code=202)
def paper_order(tenant_id: uuid.UUID, body: OrderIn, writer: TenantWriter, db: DB) -> dict:
    """PAPER only: the connector has no way to reach a live account."""
    try:
        tradable, note = B.symbol(body.symbol)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, str(e)) from None
    out = _start(db, tenant_id, writer, "paper_order",
                 f"Paper {body.side} {body.qty} {tradable}",
                 {"symbol": body.symbol, "qty": body.qty, "side": body.side},
                 body.idempotency_key)
    return {**out, "symbol": tradable, "note": note}
