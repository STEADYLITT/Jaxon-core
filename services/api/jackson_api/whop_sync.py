"""Whop records (ADR-041): memberships and sales exactly as Whop reports them.

- A paid payment becomes one `revenue_entries` row (kind actual, source whop, reference
  whop:<payment id>), recorded once even if the webhook and a sync both see it.
- Refunds become refund rows for the newly refunded amount only.
- A sale lands on a business through the checkout's metadata (Jackson's own checkout links
  carry the business id) or an owner-made product/plan link. Otherwise it is kept with no
  business, so it shows in totals but never on the wrong business.
Nothing is estimated or invented: no key, no rows.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .connectors import whop as W
from .models import Business, RevenueEntry, WhopLink, WhopMember


def _period(ts) -> str:
    s = str(ts or "")
    return s[:7] if len(s) >= 7 and s[4] == "-" else datetime.now(UTC).strftime("%Y-%m")


def business_for(db: Session, tenant_id, obj: dict) -> uuid.UUID | None:
    meta = obj.get("metadata") or {}
    raw = meta.get("jackson_business_id") if isinstance(meta, dict) else None
    if raw:
        try:
            bid = uuid.UUID(str(raw))
        except ValueError:
            bid = None
        if bid and db.scalar(select(Business.id).where(Business.id == bid,
                                                       Business.tenant_id == tenant_id)):
            return bid
    for col, val in ((WhopLink.variant_id, obj.get("variant_id")),
                     (WhopLink.product_id, obj.get("product_id"))):
        if val:
            bid = db.scalar(select(WhopLink.business_id).where(
                WhopLink.tenant_id == tenant_id, col == str(val))
                .order_by(WhopLink.created_at.desc()).limit(1))
            if bid:
                return bid
    return None


def _external(m: dict, platform: str) -> str | None:
    for a in m.get("external_accounts") or []:  # null in webhooks: keep what the sync saw
        if isinstance(a, dict) and a.get("platform") == platform and a.get("external_id"):
            return str(a["external_id"])[:64]
    return None


def upsert_member(db: Session, tenant_id, m: dict) -> bool:
    mid = str(m.get("id") or "")
    if not mid.startswith("mem_"):
        return False
    row = {"tenant_id": tenant_id, "membership_id": mid[:64],
           "business_id": business_for(db, tenant_id, m),
           "product_id": (m.get("product_id") or (m.get("product") or {}).get("id")),
           "variant_id": m.get("variant_id"), "user_id": m.get("user_id"),
           "status": str(m.get("status") or "unknown")[:24],
           "discord_id": _external(m, "discord"), "telegram_id": _external(m, "telegram"),
           "period_end": str(m.get("current_period_end") or "")[:40] or None}
    stmt = insert(WhopMember).values(id=uuid.uuid4(), **row)
    ex = stmt.excluded
    db.execute(stmt.on_conflict_do_update(
        constraint="uq_whop_member",
        set_={"status": ex.status, "product_id": ex.product_id, "variant_id": ex.variant_id,
              "user_id": func.coalesce(ex.user_id, WhopMember.user_id),
              "business_id": func.coalesce(ex.business_id, WhopMember.business_id),
              "discord_id": func.coalesce(ex.discord_id, WhopMember.discord_id),
              "telegram_id": func.coalesce(ex.telegram_id, WhopMember.telegram_id),
              "period_end": ex.period_end, "updated_at": func.now()}))
    return True


def _add(db: Session, tenant_id, business_id, kind: str, cents: int, currency: str,
         period: str, reference: str) -> bool:
    stmt = insert(RevenueEntry).values(
        id=uuid.uuid4(), tenant_id=tenant_id, business_id=business_id, kind=kind,
        amount_cents=cents, currency=currency, period=period, source="whop",
        reference=reference[:300]).on_conflict_do_nothing(
        index_elements=["tenant_id", "reference"], index_where=RevenueEntry.source == "whop"
    ).returning(RevenueEntry.id)
    return db.execute(stmt).scalar() is not None


def record_payment(db: Session, tenant_id, p: dict) -> dict:
    """Returns what was newly recorded: {"sale": bool, "refund_cents": int}."""
    pid = str(p.get("id") or "")
    out = {"sale": False, "refund_cents": 0}
    if not pid or p.get("status") != "paid":
        return out
    total = W.money_cents(p.get("total"))
    cur = W.money_currency(p.get("total"), p.get("currency"))
    biz = business_for(db, tenant_id, p)
    period = _period(p.get("created_at"))
    if total > 0:
        out["sale"] = _add(db, tenant_id, biz, "actual", total, cur, period, f"whop:{pid}")
    refunded = W.money_cents(p.get("refunded_amount"))
    if refunded > 0:
        before = int(db.scalar(select(func.coalesce(func.sum(RevenueEntry.amount_cents), 0))
                               .where(RevenueEntry.tenant_id == tenant_id,
                                      RevenueEntry.source == "whop",
                                      RevenueEntry.kind == "refund",
                                      RevenueEntry.reference.like(f"whop:{pid}:refund:%")))
                     or 0)
        if refunded > before and _add(db, tenant_id, biz, "refund", refunded - before, cur,
                                      _period(p.get("refunded_at") or p.get("updated_at")),
                                      f"whop:{pid}:refund:{refunded}"):
            out["refund_cents"] = refunded - before
    return out


def sync(db: Session, tenant_id, api: W.Whop) -> dict:
    members = sales = refunds = 0
    for m in api.memberships():
        members += upsert_member(db, tenant_id, m)
    for p in api.payments():
        r = record_payment(db, tenant_id, p)
        sales += r["sale"]
        refunds += r["refund_cents"]
    return {"members_seen": members, "new_sales": sales, "new_refund_cents": refunds}


def sync_handler(ctx):
    """Worker task `whop_sync`: pull memberships and payments from Whop."""
    from .worker import TaskResult

    api = W.client()  # NonRetryableError with the variable name when not connected
    with ctx.db_session() as db:
        out = sync(db, ctx.tenant_id, api)
        db.commit()
    access = None
    with ctx.db_session() as db:
        access = int(db.scalar(select(func.count()).select_from(WhopMember).where(
            WhopMember.tenant_id == ctx.tenant_id, WhopMember.status.in_(W.ACCESS))) or 0)
        db.rollback()
    out["members_with_access"] = access
    out["summary"] = (f"Whop sync: {out['members_seen']} memberships, {out['new_sales']} new "
                      f"sales, {access} members with access")
    return TaskResult(output=out)
