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


# --- APEX-7 results and futures data (ADR-054) -------------------------------------------------

class Trade(BaseModel):
    entry_time: str | None = Field(default=None, max_length=40)
    exit_time: str | None = Field(default=None, max_length=40)
    side: str = Field(default="long", max_length=20)
    qty: float = Field(default=1, gt=0)
    entry_price: float | None = None
    exit_price: float | None = None
    pnl: float | None = None
    fees: float | None = None


class BacktestIn(BaseModel):
    name: str = Field(default="APEX-7 run", max_length=120)
    symbol: str = Field(default="MNQ", max_length=20)
    timeframe: str = Field(default="", max_length=20)
    source: str = Field(default="APEX-7", max_length=60)
    kind: Literal["backtest", "sim"] = "backtest"
    trades: list[Trade] = Field(default_factory=list, max_length=20_000)
    csv: str | None = Field(default=None, max_length=5_000_000)
    idempotency_key: str = Field(min_length=8, max_length=200)


def _run(db, tenant_id, writer, kind, title, inp, key):
    from .runs import CommandError, create_run
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "SIMULATE", "objective": title[:300], "idempotency_key": f"{kind}-{key}"},
            first_task={"kind": kind, "title": title[:200], "tool_scope": [], "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}


@router.post("/tenants/{tenant_id}/trading/backtests", status_code=202)
def import_backtest(tenant_id: uuid.UUID, body: BacktestIn, writer: TenantWriter,
                    db: DB) -> dict:
    """APEX-7's real trades (JSON or a CSV export). Stats are computed from them only."""
    if not body.trades and not body.csv:
        raise HTTPException(422, "send the trades (JSON) or the CSV export")
    inp = body.model_dump(exclude={"idempotency_key"}, mode="json")
    return _run(db, tenant_id, writer, "apex7_import", f"Import APEX-7 {body.kind}: "
                f"{body.name}", inp, body.idempotency_key)


@router.post("/tenants/{tenant_id}/trading/backtests/pull", status_code=202)
def pull_backtests(tenant_id: uuid.UUID, body: SnapIn, writer: TenantWriter, db: DB) -> dict:
    import os
    if not os.environ.get("APEX7_RESULTS_URL"):
        raise HTTPException(409, "set APEX7_RESULTS_URL (and APEX7_RESULTS_TOKEN if it needs "
                                 "one) in Railway")
    return _run(db, tenant_id, writer, "apex7_import", "Pull APEX-7 results", {"pull": True},
                body.idempotency_key)


class BarsIn(BaseModel):
    symbol: str = Field(default="MNQ", pattern=r"^[A-Za-z0-9]{1,6}(\.[cvnCVN]\.0)?$")
    schema_: Literal["ohlcv-1d", "ohlcv-1h", "ohlcv-1m"] = Field(default="ohlcv-1d",
                                                                 alias="schema")
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/trading/futures/bars", status_code=202)
def futures_bars(tenant_id: uuid.UUID, body: BarsIn, writer: TenantWriter, db: DB) -> dict:
    """Real CME futures bars (e.g. MNQ). Databento quotes the cost; you approve it."""
    from .connectors import apex7 as X
    if not X.databento_configured():
        raise HTTPException(409, "set DATABENTO_API_KEY in Railway")
    if body.start >= body.end:
        raise HTTPException(422, "start must be before end")
    return _run(db, tenant_id, writer, "futures_bars", f"Futures bars {body.symbol} "
                f"{body.start}..{body.end}", {"symbol": body.symbol, "schema": body.schema_,
                                              "start": body.start, "end": body.end},
                body.idempotency_key)
