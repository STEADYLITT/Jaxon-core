"""Payout imports (ADR-057): clip payouts and creator earnings, pushed (CSV/JSON upload) or
pulled from the line's feed. Each new payout is recorded once in payout_lines and once as an
actual revenue entry (source import), so the money shows in Revenue as well."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .worker_errors import NonRetryableError


def _business(db, tenant_id, line: str) -> uuid.UUID | None:
    """The line's business when there is exactly one; otherwise none (never a guess)."""
    from .models import Business

    ids = db.scalars(select(Business.id).where(Business.tenant_id == tenant_id,
                                               Business.line == line).limit(2)).all()
    return ids[0] if len(ids) == 1 else None


def record(db, tenant_id, line: str, rows: list[dict], src: str, run_id=None) -> dict:
    from .models import PayoutLine, RevenueEntry

    biz = _business(db, tenant_id, line)
    new = cents = views = 0
    for p in rows:
        stmt = insert(PayoutLine).values(
            id=uuid.uuid4(), tenant_id=tenant_id, business_id=biz, line=line, source=src,
            run_id=run_id, **p).on_conflict_do_nothing(constraint="uq_payout_ref") \
            .returning(PayoutLine.id)
        if db.execute(stmt).scalar() is None:
            continue  # already recorded
        new += 1
        cents += p["amount_cents"]
        views += p["views"] or 0
        if p["amount_cents"] > 0:
            db.add(RevenueEntry(tenant_id=tenant_id, business_id=biz, kind="actual",
                                amount_cents=p["amount_cents"], currency=p["currency"],
                                period=p["period"], source="import",
                                reference=f"{line}:{p['reference']}"[:300]))
    return {"new": new, "duplicates": len(rows) - new, "amount_cents": cents, "views": views}


def import_handler(ctx):
    """input: {"line": "clips"|"creators", "pull": true} or {"line", "rows"} / {"line","csv"}"""
    from .connectors import payouts as P
    from .worker import TaskResult

    inp = ctx.input or {}
    line = str(inp.get("line") or {"clip_payouts_import": "clips",
                                    "creator_earnings_import": "creators"}.get(ctx.kind, ""))
    if line not in P.LINES:
        raise NonRetryableError("line must be clips or creators")
    if inp.get("pull"):
        raw, src = P.pull(line), "feed"
    else:
        raw = P.parse_csv(inp["csv"]) if inp.get("csv") else list(inp.get("rows") or [])
        src = "import"
    rows, skipped = P.normalize(raw, line)
    if not rows:
        raise NonRetryableError("no usable payouts: each needs an amount and a clip URL or "
                                "creator name")
    with ctx.db_session() as db:
        out = record(db, ctx.tenant_id, line, rows, src, ctx.run_id)
        db.commit()
    what = P.LINES[line]["what"]
    seen = f", {out['views']:,} views" if line == "clips" and out["views"] else ""
    summary = (f"{out['new']} new {what} (${out['amount_cents'] / 100:,.2f}{seen}); "
               f"{out['duplicates']} already recorded; {skipped} skipped (no amount or "
               f"clip/creator)")
    return TaskResult({"line": line, "source": src, "skipped": skipped, **out,
                       "summary": summary})
