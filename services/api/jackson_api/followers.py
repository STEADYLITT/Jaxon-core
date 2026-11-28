"""Follower analytics (ADR-058): take today's real readings, keep one row per account per
day, and work out growth from two real readings only."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .worker_errors import NonRetryableError


def record(db, tenant_id, readings: list[dict], on: date | None = None) -> int:
    from .models import FollowerSnapshot

    day = on or datetime.now(UTC).date()
    for r in readings:
        stmt = insert(FollowerSnapshot).values(id=uuid.uuid4(), tenant_id=tenant_id,
                                               taken_on=day, **r)
        db.execute(stmt.on_conflict_do_update(
            constraint="uq_follower_day",
            set_={"followers": stmt.excluded.followers, "handle": stmt.excluded.handle}))
    return len(readings)


def growth(db, tenant_id, days: int = 30) -> dict:
    """Per account: first and latest reading inside the window. Gained is only reported
    for accounts with two different days of readings."""
    from .models import FollowerSnapshot as F

    since = datetime.now(UTC).date() - timedelta(days=days)
    rows = db.execute(select(F.platform, F.account_id, F.handle, F.followers, F.taken_on)
                      .where(F.tenant_id == tenant_id, F.taken_on >= since)
                      .order_by(F.taken_on)).all()
    acc: dict[tuple, dict] = {}
    for p, a, h, n, d in rows:
        x = acc.setdefault((p, a), {"platform": p, "account_id": a, "handle": h,
                                    "first": n, "first_on": d})
        x.update(latest=n, latest_on=d, handle=h or x["handle"])
    measured = [x for x in acc.values() if x["latest_on"] > x["first_on"]]
    return {"accounts": list(acc.values()), "followers_now": sum(x["latest"] for x in
                                                                  acc.values()),
            "gained": (sum(x["latest"] - x["first"] for x in measured) if measured else None),
            "first_day": min((x["first_on"] for x in acc.values()), default=None)}


def has_any(db, tenant_id) -> bool:
    from .models import FollowerSnapshot

    return bool(db.scalar(select(func.count()).select_from(FollowerSnapshot).where(
        FollowerSnapshot.tenant_id == tenant_id)))


def snapshot_handler(ctx):
    from .connectors import analytics as A
    from .worker import TaskResult

    readings, errors = A.read_all()
    if not readings:
        raise NonRetryableError("no follower counts read: " + "; ".join(
            f"{p}: {e}" for p, e in errors.items()) if errors else "no accounts to read")
    with ctx.db_session() as db:
        record(db, ctx.tenant_id, readings)
        db.commit()
        g = growth(db, ctx.tenant_id)
    return TaskResult({"readings": readings, "errors": errors, "followers_now":
                       g["followers_now"], "gained_30d": g["gained"],
                       "summary": f"{len(readings)} account(s), {g['followers_now']:,} "
                                  f"followers now" + (f", {g['gained']:+,} in 30 days"
                                                      if g["gained"] is not None else
                                                      " (growth shows from the next day)")
                                  + (f"; not read: {', '.join(errors)}" if errors else "")})


def daily_sweep(db) -> list:
    """Once a day, for every tenant that has started tracking (one snapshot taken), take
    today's readings. Runs inside the worker's launch sweep."""
    from .connectors import analytics as A
    from .models import FollowerSnapshot as F

    if not A.configured():
        return []
    today = datetime.now(UTC).date()
    tenants = db.scalars(select(F.tenant_id).group_by(F.tenant_id)
                         .having(func.max(F.taken_on) < today)).all()
    if not tenants:
        return []
    readings, _errors = A.read_all()
    for t in tenants:
        record(db, t, readings, today)
    return list(tenants)
