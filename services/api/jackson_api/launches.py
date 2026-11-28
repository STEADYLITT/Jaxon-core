"""Launch approvals (phase 21, ADR-032): one owner approval per launch, then autonomy
inside its scope.

A launch fixes: total budget + daily spend cap, a time window, the channels allowed
(Shopify publishing, named social accounts, email to named lists, ad spend) and
kill rules. `covers()` answers "is this action inside an active launch's scope?" for the
policy engine. `check_health()` pauses a launch on anomalies and stops it when a kill
rule fires. The scope itself can only be set by the owner through the API; the worker
can insert proposals and move an active launch to a stopped state, never widen it.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import Launch, LaunchSignal, LedgerEntry, Task, WorkflowRun
from .policy import Action, Category

# Categories a launch can never cover: they always go to the approvals inbox (or are
# refused outright, like live trades).
NEVER_IN_SCOPE = frozenset({Category.PAYMENT, Category.ACCOUNT_CREATION,
                            Category.SENSITIVE_DATA, Category.LIVE_TRADE})
ROUTINE = frozenset({Category.READ, Category.DRAFT, Category.SIMULATION,
                     Category.SANDBOX_CHANGE})

# Anomaly thresholds (auto-pause). Conservative on purpose: a pause costs a click.
SPEND_SPIKE_SHARE = 0.5        # >50% of the daily cap spent within one hour
ERRORS_PER_HOUR = 5            # failed tasks in the launch's runs within one hour
BOUNCE_RATE, MIN_SENDS_BOUNCE = 0.05, 20
COMPLAINT_RATE, MIN_SENDS_COMPLAINT = 0.003, 50
REFUND_RATE, MIN_SALES_REFUND = 0.20, 5


def now() -> datetime:
    return datetime.now(UTC)


def _channel_ok(launch: Launch, action: Action) -> tuple[bool, str]:
    ch = launch.channels or {}
    c = action.category
    if c in ROUTINE:
        return True, "routine work inside the launch"
    if c == Category.PRODUCTION_CHANGE:
        if action.channel == "shopify_publish" and ch.get("shopify_publish"):
            return True, "Shopify publishing is allowed by the launch"
        if action.channel == "site_publish" and ch.get("site_publish"):
            return True, "publishing the launch's site is allowed by the launch"
        return False, "this production change isn't one of the launch's channels"
    if c == Category.PUBLIC_POST:
        accounts = (ch.get("social") or {}).get("accounts") or []
        if action.channel == "social" and action.target in accounts:
            return True, f"posting to {action.target} is allowed by the launch"
        return False, f"posting to {action.target or 'this account'} isn't in the launch"
    if c == Category.OUTBOUND_CAMPAIGN:
        lists = (ch.get("email") or {}).get("lists") or []
        if action.channel == "email" and action.target in lists:
            return True, f"email to list '{action.target}' is allowed by the launch"
        return False, f"email to '{action.target or 'this list'}' isn't in the launch"
    if c == Category.SPEND:
        if action.channel == "ads" and not ch.get("ads"):
            return False, "ad spend isn't one of the launch's channels"
        return True, "spend inside the launch"
    return False, f"{c} is never covered by a launch"


def spend_so_far(db: Session, launch_id: uuid.UUID, since: datetime | None = None) -> int:
    """Money committed or still held by the launch's runs (reserve - release)."""
    q = (select(LedgerEntry.kind, func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
         .join(WorkflowRun, WorkflowRun.id == LedgerEntry.run_id)
         .where(WorkflowRun.launch_id == launch_id))
    if since is not None:
        q = q.where(LedgerEntry.created_at >= since)
    sums = dict(db.execute(q.group_by(LedgerEntry.kind)).all())
    return int(sums.get("reserve", 0)) - int(sums.get("release", 0))


def covers(db: Session, launch_id: uuid.UUID, business_id, action: Action,
           lock: bool = False) -> tuple[bool, str]:
    """Is `action` inside the scope of this launch, right now?"""
    if action.category in NEVER_IN_SCOPE:
        return False, f"{action.category} is never covered by a launch approval"
    launch = db.scalar(select(Launch).where(Launch.id == launch_id))
    if launch is None:
        return False, "launch not found"
    if lock and launch.status == "active":  # serialize spend decisions for the launch
        db.scalar(select(Launch.id).where(Launch.id == launch_id).with_for_update())
        db.refresh(launch)
    if launch.business_id != business_id:
        return False, "the run's business isn't the launch's business"
    if launch.status != "active":
        return False, f"launch '{launch.title}' is {launch.status}" + (
            f" ({launch.status_reason})" if launch.status_reason else "")
    t = now()
    if not (launch.starts_at <= t < launch.ends_at):
        return False, f"launch '{launch.title}' is outside its time window"
    ok, why = _channel_ok(launch, action)
    if not ok:
        return False, why
    if action.amount_cents:
        total = spend_so_far(db, launch.id)
        day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
        today = spend_so_far(db, launch.id, since=day_start)
        if total + action.amount_cents > launch.total_budget_cents:
            return False, (f"would exceed the launch budget ({total + action.amount_cents}c > "
                           f"{launch.total_budget_cents}c)")
        if today + action.amount_cents > launch.daily_cap_cents:
            return False, (f"would exceed the daily cap ({today + action.amount_cents}c > "
                           f"{launch.daily_cap_cents}c)")
    return True, f"{why} (launch '{launch.title}' v{launch.version})"


# --- Health: anomalies pause, kill rules stop ---------------------------------------------

def _signals(db: Session, launch_id, since: datetime | None = None) -> dict:
    q = select(LaunchSignal.kind, func.sum(LaunchSignal.quantity),
               func.sum(LaunchSignal.amount_cents)).where(LaunchSignal.launch_id == launch_id)
    if since is not None:
        q = q.where(LaunchSignal.created_at >= since)
    return {k: {"count": int(n or 0), "amount_cents": int(a or 0)}
            for k, n, a in db.execute(q.group_by(LaunchSignal.kind))}


def metrics(db: Session, launch: Launch) -> dict:
    t = now()
    sig = _signals(db, launch.id)
    sales = sig.get("sale", {}).get("count", 0)
    spend = spend_so_far(db, launch.id)
    return {
        "spend_cents": spend,
        "spend_today_cents": spend_so_far(
            db, launch.id, since=t.replace(hour=0, minute=0, second=0, microsecond=0)),
        "spend_last_hour_cents": spend_so_far(db, launch.id, since=t - timedelta(hours=1)),
        "budget_left_cents": max(launch.total_budget_cents - spend, 0),
        "sales": sales, "revenue_cents": sig.get("sale", {}).get("amount_cents", 0),
        "refunds": sig.get("refund", {}).get("count", 0),
        "sends": sig.get("send", {}).get("count", 0),
        "bounces": sig.get("bounce", {}).get("count", 0),
        "complaints": sig.get("complaint", {}).get("count", 0),
        "cac_cents": round(spend / sales) if sales else None,
        "errors_last_hour": int(db.scalar(
            select(func.count()).select_from(Task).join(WorkflowRun, WorkflowRun.id == Task.run_id)
            .where(WorkflowRun.launch_id == launch.id, Task.status == "failed",
                   Task.updated_at >= t - timedelta(hours=1))) or 0) + _signals(
            db, launch.id, since=t - timedelta(hours=1)).get("platform_error", {}).get(
            "count", 0),
        "days_running": max((t - launch.starts_at).days, 0) if launch.starts_at else 0,
        "days_left": max((launch.ends_at - t).days, 0) if launch.ends_at else 0,
    }


def verdict(launch: Launch, m: dict) -> tuple[str, str] | None:
    """Pure: (new_status, reason) when an anomaly or kill rule fires, else None."""
    if launch.ends_at and now() >= launch.ends_at:
        return "expired", "the launch's time window ended"
    rules = launch.kill_rules or {}
    if launch.total_budget_cents and m["spend_cents"] >= launch.total_budget_cents:
        return "completed", "the launch budget is used up"
    if rules.get("max_cac_cents"):
        cac = m["cac_cents"] if m["sales"] else (m["spend_cents"] or None)
        if cac is not None and cac > rules["max_cac_cents"]:
            return "killed", (f"kill rule: cost per sale {cac}c is above "
                              f"{rules['max_cac_cents']}c")
    zero_days = rules.get("zero_sales_after_days")
    if zero_days and m["sales"] == 0 and m["days_running"] >= zero_days:
        return "killed", f"kill rule: no sales after {zero_days} days"
    if launch.daily_cap_cents and m["spend_last_hour_cents"] > \
            SPEND_SPIKE_SHARE * launch.daily_cap_cents:
        return "paused", (f"anomaly: {m['spend_last_hour_cents']}c spent in the last hour "
                          f"(over half the daily cap)")
    if m["errors_last_hour"] >= ERRORS_PER_HOUR:
        return "paused", f"anomaly: {m['errors_last_hour']} platform errors in the last hour"
    if m["sends"] >= MIN_SENDS_BOUNCE and m["bounces"] / m["sends"] > BOUNCE_RATE:
        return "paused", f"anomaly: bounce rate {m['bounces'] / m['sends']:.0%}"
    if m["sends"] >= MIN_SENDS_COMPLAINT and m["complaints"] / m["sends"] > COMPLAINT_RATE:
        return "paused", f"anomaly: complaint rate {m['complaints'] / m['sends']:.1%}"
    if m["sales"] >= MIN_SALES_REFUND and m["refunds"] / m["sales"] > REFUND_RATE:
        return "paused", f"anomaly: refund rate {m['refunds'] / m['sales']:.0%}"
    return None


def check_health(db: Session, launch: Launch) -> str | None:
    """Apply `verdict` to an active launch. Returns the new status if it changed. The
    caller commits. Works for both the API and the worker roles."""
    if launch.status != "active":
        return None
    v = verdict(launch, metrics(db, launch))
    if v is None:
        return None
    status, reason = v
    db.execute(update(Launch).where(Launch.id == launch.id, Launch.status == "active")
               .values(status=status, status_reason=reason, updated_at=func.now()))
    from .models import AuditEvent

    db.add(AuditEvent(tenant_id=launch.tenant_id, actor_user_id=None,
                      action=f"launch.{status}", entity_type="Launch",
                      entity_id=launch.id, data={"reason": reason, "by": "jackson"}))
    launch.status, launch.status_reason = status, reason
    return status


def sweep(db: Session) -> list[tuple[str, str]]:
    """Check every active launch (worker loop + daily report). Returns what changed."""
    changed = []
    for launch in db.scalars(select(Launch).where(Launch.status == "active")).all():
        s = check_health(db, launch)
        if s:
            changed.append((str(launch.id), s))
    return changed
