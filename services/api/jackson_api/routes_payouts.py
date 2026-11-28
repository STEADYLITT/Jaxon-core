"""Clip payouts and creator earnings (ADR-057): upload a CSV/JSON statement, or pull the
line's feed. Both run as a task, so the import is traced like any other work."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .deps import DB, TenantWriter

router = APIRouter(tags=["payouts"])
Line = Literal["clips", "creators"]
KIND = {"clips": "clip_payouts_import", "creators": "creator_earnings_import"}


def _run(db, tenant_id, writer, title, inp, key):
    from .runs import CommandError, create_run

    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "REPORT", "objective": title[:300],
            "idempotency_key": f"payouts-{key}"},
            first_task={"kind": KIND[inp["line"]], "title": title[:200], "tool_scope": [],
                        "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}


class ImportIn(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20_000)
    csv: str | None = Field(default=None, max_length=5_000_000)
    idempotency_key: str = Field(min_length=8, max_length=200)


class PullIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/payouts/{line}/import", status_code=202)
def import_payouts(tenant_id: uuid.UUID, line: Line, body: ImportIn, writer: TenantWriter,
                   db: DB) -> dict:
    """A statement exactly as the platform exported it (CSV) or as JSON rows."""
    if not body.rows and not body.csv:
        raise HTTPException(422, "send the payouts (JSON rows) or the CSV export")
    what = "clip payouts" if line == "clips" else "creator earnings"
    return _run(db, tenant_id, writer, f"Import {what}",
                {"line": line, "rows": body.rows, "csv": body.csv}, body.idempotency_key)


@router.post("/tenants/{tenant_id}/payouts/{line}/pull", status_code=202)
def pull_payouts(tenant_id: uuid.UUID, line: Line, body: PullIn, writer: TenantWriter,
                 db: DB) -> dict:
    from .connectors import payouts as P

    cfg = P.LINES[line]
    if not P.feed_url(line):
        raise HTTPException(409, f"set {cfg['source']} to your {cfg['what']} feed's https "
                                 f"address in Railway (or upload a CSV)")
    return _run(db, tenant_id, writer, f"Pull {cfg['what']}", {"line": line, "pull": True},
                body.idempotency_key)
