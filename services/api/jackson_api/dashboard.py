"""Owner dashboard, metrics, alerts, global stop and daily reports (spec vol. 04, phase 19,
ADR-029).

Everything here is computed from canonical rows. Nothing is estimated unless it's labeled
as an estimate. Actual and estimated revenue are never added together.
"""

import json
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    AgentSpec,
    Approval,
    Artifact,
    AuditEvent,
    BrowserSession,
    Business,
    ContentItem,
    LedgerEntry,
    ModelUsage,
    Opportunity,
    OutreachCampaign,
    OutreachMessage,
    RevenueEntry,
    ShopProduct,
    SiteProject,
    Task,
    Tenant,
    ToolGrant,
    WorkflowRun,
)

TERMINAL = ("succeeded", "failed", "canceled")


def _iso(d):
    return d.isoformat() if d else None


def _sum(db, q) -> int:
    return int(db.scalar(q) or 0)


# --- Portfolio P&L ------------------------------------------------------------------------------

def portfolio(db: Session, tenant_id) -> dict:
    """Per business and in total: actual revenue, refunds, estimated revenue (kept apart),
    spend actually committed, money still held in reservations, and model cost."""
    businesses = {b.id: b.name for b in db.scalars(select(Business).where(
        Business.tenant_id == tenant_id))}
    rows = {}

    def row(bid):
        return rows.setdefault(bid, {
            "business_id": str(bid) if bid else None,
            "business": businesses.get(bid, "(no business)") if bid else "(no business)",
            "actual_revenue_cents": 0, "refunds_cents": 0, "estimated_revenue_cents": 0,
            "spend_cents": 0, "held_cents": 0, "model_cost_cents": 0})

    for bid, kind, amount in db.execute(
            select(RevenueEntry.business_id, RevenueEntry.kind,
                   func.sum(RevenueEntry.amount_cents))
            .where(RevenueEntry.tenant_id == tenant_id)
            .group_by(RevenueEntry.business_id, RevenueEntry.kind)):
        key = {"actual": "actual_revenue_cents", "refund": "refunds_cents",
               "estimate": "estimated_revenue_cents"}[kind]
        row(bid)[key] += int(amount)
    for bid, kind, amount in db.execute(
            select(WorkflowRun.business_id, LedgerEntry.kind, func.sum(LedgerEntry.amount_cents))
            .join(WorkflowRun, WorkflowRun.id == LedgerEntry.run_id)
            .where(LedgerEntry.tenant_id == tenant_id)
            .group_by(WorkflowRun.business_id, LedgerEntry.kind)):
        r = row(bid)
        if kind == "commit":
            r["spend_cents"] += int(amount)
            r["held_cents"] -= int(amount)
        elif kind == "reserve":
            r["held_cents"] += int(amount)
        else:
            r["held_cents"] -= int(amount)
    for bid, micros in db.execute(
            select(WorkflowRun.business_id, func.sum(ModelUsage.cost_usd_micros))
            .join(WorkflowRun, WorkflowRun.id == ModelUsage.run_id)
            .where(ModelUsage.tenant_id == tenant_id)
            .group_by(WorkflowRun.business_id)):
        row(bid)["model_cost_cents"] += round(int(micros or 0) / 10_000)
    for bid in businesses:
        row(bid)
    out = []
    for r in rows.values():
        r["actual_profit_cents"] = (r["actual_revenue_cents"] - r["refunds_cents"]
                                    - r["spend_cents"] - r["model_cost_cents"])
        out.append(r)
    out.sort(key=lambda r: r["business"])
    total = {k: sum(r[k] for r in out) for k in (
        "actual_revenue_cents", "refunds_cents", "estimated_revenue_cents", "spend_cents",
        "held_cents", "model_cost_cents", "actual_profit_cents")}
    return {"businesses": out, "total": total,
            "note": "actual profit = actual revenue - refunds - spend - model cost. "
                    "Estimates are shown separately and never added in."}


# --- Metrics ------------------------------------------------------------------------------------

def metrics(db: Session, tenant_id, days: int = 30) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    counts = dict(db.execute(select(WorkflowRun.status, func.count()).where(
        WorkflowRun.tenant_id == tenant_id, WorkflowRun.created_at >= since)
        .group_by(WorkflowRun.status)).all())
    ok, bad = counts.get("succeeded", 0), counts.get("failed", 0)
    finished = ok + bad + counts.get("canceled", 0)
    spend = _sum(db, select(func.sum(LedgerEntry.amount_cents)).where(
        LedgerEntry.tenant_id == tenant_id, LedgerEntry.kind == "commit",
        LedgerEntry.created_at >= since))
    model_micros = _sum(db, select(func.sum(ModelUsage.cost_usd_micros)).where(
        ModelUsage.tenant_id == tenant_id, ModelUsage.created_at >= since))
    latency = db.scalar(select(func.avg(func.extract(
        "epoch", WorkflowRun.finished_at - WorkflowRun.started_at))).where(
        WorkflowRun.tenant_id == tenant_id, WorkflowRun.created_at >= since,
        WorkflowRun.finished_at.is_not(None), WorkflowRun.started_at.is_not(None)))
    wait = db.scalar(select(func.avg(func.extract(
        "epoch", Approval.decided_at - Approval.created_at))).where(
        Approval.tenant_id == tenant_id, Approval.created_at >= since,
        Approval.decided_at.is_not(None)))
    refunds = _sum(db, select(func.sum(RevenueEntry.amount_cents)).where(
        RevenueEntry.tenant_id == tenant_id, RevenueEntry.kind == "refund"))
    actual = _sum(db, select(func.sum(RevenueEntry.amount_cents)).where(
        RevenueEntry.tenant_id == tenant_id, RevenueEntry.kind == "actual"))
    return {
        "window_days": days, "runs": counts,
        "verified_success_rate": round(ok / (ok + bad), 3) if ok + bad else None,
        "failure_rate": round(bad / finished, 3) if finished else None,
        "cost_per_run_cents": round((spend + model_micros / 10_000) / finished)
        if finished else None,
        "avg_latency_seconds": round(float(latency), 1) if latency is not None else None,
        "avg_approval_wait_seconds": round(float(wait), 1) if wait is not None else None,
        "gross_margin_pct": round((actual - refunds - spend) / actual, 3) if actual else None,
        "refund_rate": round(refunds / actual, 3) if actual else None,
        "conversion_rate": None, "customer_acquisition_cost_cents": None,
        "not_measured": ["conversion_rate", "customer_acquisition_cost_cents"],
        "not_measured_reason": "needs a storefront/analytics connector (not connected)",
    }


# --- Connectors ---------------------------------------------------------------------------------

def connectors(db: Session, tenant_id) -> list[dict]:
    """What is live and what is a stand-in. A mock is never reported as integrated."""
    model_live = bool(os.environ.get("ANTHROPIC_API_KEY"))
    shop_modes = {(g.config or {}).get("mode", "mock") for g in db.scalars(
        select(ToolGrant).where(ToolGrant.tenant_id == tenant_id,
                                ToolGrant.tool_name == "shopify.drafts",
                                ToolGrant.status == "active"))}
    shop_live = "shopify" in shop_modes and bool(os.environ.get("SHOPIFY_ADMIN_API_TOKEN"))
    rows = [
        ("models", "Claude (model gateway)", model_live, "ANTHROPIC_API_KEY"),
        ("shopify", "Shopify drafts", shop_live, "dev store + SHOPIFY_ADMIN_API_TOKEN"),
        ("social", "Social posting", False, "posting provider key (e.g. Upload-Post)"),
        ("hosting", "Website hosting", False, "hosting account token"),
        ("dns", "Domain verification", os.environ.get("SITE_DOMAIN_VERIFY") == "dns",
         "SITE_DOMAIN_VERIFY=dns"),
        ("outreach", "Email/SMS sending", False, "email/SMS provider key"),
        ("artifacts", "Artifact storage", bool(os.environ.get("S3_BUCKET")),
         "S3 bucket (local disk until then)"),
    ]
    return [{"id": i, "name": n, "mode": "live" if live else "mock", "needs": None if live
             else need} for i, n, live, need in rows]


# --- Alerts -------------------------------------------------------------------------------------

def alerts(db: Session, tenant_id) -> list[dict]:
    now = datetime.now(UTC)
    out = []
    t = db.get(Tenant, tenant_id)
    if t is not None and t.stopped_at:
        out.append({"level": "critical", "kind": "global_stop",
                    "message": f"Global stop is on since {t.stopped_at:%Y-%m-%d %H:%M} UTC: "
                               f"{t.stop_reason or ''}".strip()})
    for kind, n in db.execute(select(Task.kind, func.count()).where(
            Task.tenant_id == tenant_id, Task.status == "failed",
            Task.updated_at >= now - timedelta(hours=24)).group_by(Task.kind)):
        if n >= 3:
            out.append({"level": "warning", "kind": "repeated_failures",
                        "message": f"'{kind}' failed {n} times in the last 24 hours"})
    old = db.scalar(select(func.count()).where(
        Approval.tenant_id == tenant_id, Approval.status == "pending",
        Approval.created_at < now - timedelta(hours=24)))
    if old:
        out.append({"level": "warning", "kind": "stale_approvals",
                    "message": f"{old} approval(s) waiting more than 24 hours"})
    limit_usd = float(os.environ.get("MODEL_MONTHLY_BUDGET_USD", "20"))
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = _sum(db, select(func.sum(ModelUsage.cost_usd_micros)).where(
        ModelUsage.tenant_id == tenant_id, ModelUsage.created_at >= month)) / 1e6
    if limit_usd and used >= 0.8 * limit_usd:
        out.append({"level": "warning", "kind": "model_budget",
                    "message": f"model spend ${used:.2f} is {used / limit_usd:.0%} of the "
                               f"${limit_usd:.2f} monthly budget"})
    unconfirmed = db.scalar(select(func.count()).where(
        OutreachMessage.tenant_id == tenant_id, OutreachMessage.status == "sending"))
    if unconfirmed:
        out.append({"level": "warning", "kind": "unconfirmed_sends",
                    "message": f"{unconfirmed} outreach message(s) handed to the sender but "
                               "not confirmed; check before re-sending"})
    if os.environ.get("ANTHROPIC_API_KEY"):
        last = db.scalar(select(func.max(ModelUsage.created_at)).where(
            ModelUsage.tenant_id == tenant_id, ModelUsage.mode == "live"))
        if last is None or last < now - timedelta(days=7):
            out.append({"level": "info", "kind": "stale_integration",
                        "message": "Claude is configured but hasn't been used live in 7 days"})
    return out


# --- Whole dashboard ----------------------------------------------------------------------------

def build(db: Session, tenant_id) -> dict:
    t = db.get(Tenant, tenant_id)
    runs = db.scalars(select(WorkflowRun).where(WorkflowRun.tenant_id == tenant_id)
                      .order_by(WorkflowRun.created_at.desc()).limit(20)).all()
    run_ids = [r.id for r in runs]
    cost = {rid: 0 for rid in run_ids}
    if run_ids:
        for rid, c in db.execute(select(LedgerEntry.run_id, func.sum(LedgerEntry.amount_cents))
                                 .where(LedgerEntry.run_id.in_(run_ids),
                                        LedgerEntry.kind == "commit")
                                 .group_by(LedgerEntry.run_id)):
            cost[rid] += int(c)
        for rid, m in db.execute(select(ModelUsage.run_id, func.sum(ModelUsage.cost_usd_micros))
                                 .where(ModelUsage.run_id.in_(run_ids))
                                 .group_by(ModelUsage.run_id)):
            cost[rid] += round(int(m or 0) / 10_000)
    agent_rows = db.scalars(select(AgentSpec).where(AgentSpec.tenant_id == tenant_id)
                            .order_by(AgentSpec.name, AgentSpec.version.desc())).all()
    agent_tasks = {}
    if agent_rows:
        for sid, st, n in db.execute(select(Task.agent_spec_id, Task.status, func.count())
                                     .where(Task.agent_spec_id.in_([a.id for a in agent_rows]))
                                     .group_by(Task.agent_spec_id, Task.status)):
            agent_tasks.setdefault(sid, {})[st] = n
    opp_counts = dict(db.execute(select(Opportunity.status, func.count()).where(
        Opportunity.tenant_id == tenant_id).group_by(Opportunity.status)).all())
    top = db.scalars(select(Opportunity).where(Opportunity.tenant_id == tenant_id,
                                               Opportunity.status != "rejected")
                     .order_by(Opportunity.score.desc()).limit(5)).all()
    pending = db.scalars(select(Approval).where(Approval.tenant_id == tenant_id,
                                                Approval.status == "pending")
                         .order_by(Approval.created_at)).all()
    sessions = db.scalars(select(BrowserSession).where(
        BrowserSession.tenant_id == tenant_id,
        BrowserSession.status.in_(["active", "paused", "taken_over"]))).all()
    arts = db.scalars(select(Artifact).where(Artifact.tenant_id == tenant_id)
                      .order_by(Artifact.created_at.desc()).limit(10)).all()
    audit_rows = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
                            .order_by(AuditEvent.created_at.desc()).limit(20)).all()
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "stop": {"active": bool(t and t.stopped_at), "since": _iso(t.stopped_at) if t else None,
                 "reason": t.stop_reason if t else None},
        "portfolio": portfolio(db, tenant_id),
        "metrics": metrics(db, tenant_id),
        "alerts": alerts(db, tenant_id),
        "opportunities": {"by_status": opp_counts, "top": [
            {"id": str(o.id), "title": o.title, "score": o.score, "status": o.status,
             "has_economics": bool(o.economics)} for o in top]},
        "agents": [{"id": str(a.id), "name": a.name, "version": a.version, "status": a.status,
                    "tasks": agent_tasks.get(a.id, {})} for a in agent_rows],
        "runs": [{"id": str(r.id), "objective": r.objective, "type": r.command_type,
                  "status": r.status, "paused": r.paused, "cost_cents": cost[r.id],
                  "created_at": _iso(r.created_at), "started_at": _iso(r.started_at),
                  "finished_at": _iso(r.finished_at)} for r in runs],
        "approvals": {"pending": len(pending), "items": [
            {"id": str(a.id), "run_id": str(a.run_id), "category": a.category,
             "description": a.description, "amount_cents": a.amount_cents,
             "created_at": _iso(a.created_at)} for a in pending[:20]]},
        "live_visual_work": [{"id": str(s.id), "run_id": str(s.run_id), "status": s.status,
                              "control": s.control, "url": s.current_url,
                              "last_action": s.last_action} for s in sessions],
        "artifacts": [{"id": str(a.id), "run_id": str(a.run_id) if a.run_id else None,
                       "kind": a.kind, "content_type": a.content_type,
                       "size_bytes": a.size_bytes, "type": (a.meta or {}).get("type"),
                       "created_at": _iso(a.created_at)} for a in arts],
        "connectors": connectors(db, tenant_id),
        "audit": [{"action": e.action, "entity": e.entity_type,
                   "entity_id": str(e.entity_id) if e.entity_id else None,
                   "at": _iso(e.created_at)} for e in audit_rows],
    }


# --- Global stop --------------------------------------------------------------------------------

def global_stop(db: Session, tenant_id, user_id, reason: str) -> dict:
    """Revoke new work, cancel every unfinished run, stand down scheduled and in-progress
    outward actions, and list external effects that may already have happened so the owner
    can reconcile them. The DB triggers then refuse new runs and task starts."""
    from .contracts import CommandType
    from .runs import control_run

    t = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    already = t.stopped_at is not None
    t.stopped_at = t.stopped_at or func.now()
    t.stopped_by, t.stop_reason = user_id, reason
    db.flush()
    canceled = []
    was_running = []
    for run in db.scalars(select(WorkflowRun).where(WorkflowRun.tenant_id == tenant_id,
                                                    WorkflowRun.status.not_in(TERMINAL))):
        if db.scalar(select(func.count()).where(Task.run_id == run.id,
                                                Task.status == "running")):
            was_running.append(str(run.id))
        control_run(db, run, CommandType.STOP, user_id)
        canceled.append(str(run.id))
    content = db.scalars(select(ContentItem).where(ContentItem.tenant_id == tenant_id,
                                                   ContentItem.status == "scheduled")).all()
    for c in content:
        c.status = "approved"  # back to approved-but-unscheduled; owner reschedules
    campaigns = db.scalars(select(OutreachCampaign).where(
        OutreachCampaign.tenant_id == tenant_id,
        OutreachCampaign.status.in_(["approved", "sending"]))).all()
    for k in campaigns:
        k.status = "cancelled"
    sessions = db.scalars(select(BrowserSession).where(
        BrowserSession.tenant_id == tenant_id,
        BrowserSession.status.in_(["active", "paused", "taken_over"]))).all()
    for s in sessions:
        s.control = "terminate"  # the watching worker closes it (phase 11 owner control)
    since = datetime.now(UTC) - timedelta(hours=24)
    reconcile = {
        "runs_that_were_mid_task": was_running,
        "outreach_unconfirmed": db.scalar(select(func.count()).where(
            OutreachMessage.tenant_id == tenant_id, OutreachMessage.status == "sending")),
        "outreach_sent_last_24h": db.scalar(select(func.count()).where(
            OutreachMessage.tenant_id == tenant_id, OutreachMessage.status == "sent",
            OutreachMessage.sent_at >= since)),
        "content_published_last_24h": [str(i) for i in db.scalars(select(ContentItem.id).where(
            ContentItem.tenant_id == tenant_id, ContentItem.status == "published",
            ContentItem.updated_at >= since))],
        "products_live": [p.external_id for p in db.scalars(select(ShopProduct).where(
            ShopProduct.tenant_id == tenant_id, ShopProduct.status == "ACTIVE"))],
        "sites_live": [s.live_url for s in db.scalars(select(SiteProject).where(
            SiteProject.tenant_id == tenant_id, SiteProject.status == "live"))],
        "money_held_cents": portfolio(db, tenant_id)["total"]["held_cents"],
    }
    summary = {"already_stopped": already, "runs_canceled": canceled,
               "content_unscheduled": len(content), "campaigns_cancelled": len(campaigns),
               "browser_sessions_terminated": len(sessions), "reconcile": reconcile,
               "note": "Live products and sites stay up: taking them down is a separate, "
                       "approved production change. Review the reconcile list."}
    db.add(AuditEvent(tenant_id=tenant_id, actor_user_id=user_id, action="tenant.global_stop",
                      entity_type="Tenant", entity_id=tenant_id,
                      data={"reason": reason, **{k: v for k, v in summary.items()
                                                 if k != "note"}}))
    return summary


def resume(db: Session, tenant_id, user_id) -> None:
    t = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    t.stopped_at = t.stopped_by = t.stop_reason = None
    db.add(AuditEvent(tenant_id=tenant_id, actor_user_id=user_id, action="tenant.resumed",
                      entity_type="Tenant", entity_id=tenant_id, data={}))


# --- Daily report -------------------------------------------------------------------------------

def daily_report(db: Session, tenant_id, day: datetime | None = None) -> dict:
    """Outcomes, blockers and next actions for the last 24 hours (spec vol. 04)."""
    end = day or datetime.now(UTC)
    start = end - timedelta(hours=24)
    finished = db.scalars(select(WorkflowRun).where(
        WorkflowRun.tenant_id == tenant_id, WorkflowRun.finished_at >= start,
        WorkflowRun.finished_at < end)).all()
    failed_tasks = db.scalars(select(Task).where(
        Task.tenant_id == tenant_id, Task.status == "failed", Task.updated_at >= start)).all()
    dash = build(db, tenant_id)
    outcomes = [f"{r.objective} ({r.status})" for r in finished if r.status == "succeeded"]
    blockers = ([f"{t.kind}: {(t.error or '')[:160]}" for t in failed_tasks]
                + [a["message"] for a in dash["alerts"] if a["level"] != "info"])
    next_actions = [f"Decide approval: {a['description']}" for a in dash["approvals"]["items"]]
    next_actions += [f"Connect {c['name']} ({c['needs']})" for c in dash["connectors"]
                     if c["mode"] == "mock"][:3]
    report = {"type": "daily_report", "period_start": start.isoformat(),
              "period_end": end.isoformat(), "outcomes": outcomes, "blockers": blockers,
              "next_actions": next_actions,
              "numbers": {"runs_finished": len(finished),
                          "runs_succeeded": len(outcomes),
                          "pending_approvals": dash["approvals"]["pending"],
                          "actual_profit_cents": dash["portfolio"]["total"]
                          ["actual_profit_cents"],
                          "estimated_revenue_cents": dash["portfolio"]["total"]
                          ["estimated_revenue_cents"]}}
    lines = [f"# Jackson daily report — {end:%Y-%m-%d}", "", "## Outcomes"]
    lines += [f"- {o}" for o in outcomes] or ["- No runs finished."]
    lines += ["", "## Blockers"] + ([f"- {b}" for b in blockers] or ["- None."])
    lines += ["", "## Next actions"] + ([f"- {n}" for n in next_actions] or ["- None."])
    n = report["numbers"]
    lines += ["", "## Numbers", f"- Runs finished: {n['runs_finished']} "
              f"({n['runs_succeeded']} succeeded)",
              f"- Pending approvals: {n['pending_approvals']}",
              f"- Actual profit to date: ${n['actual_profit_cents'] / 100:,.2f}",
              f"- Estimated revenue (not actual): ${n['estimated_revenue_cents'] / 100:,.2f}"]
    report["markdown"] = "\n".join(lines) + "\n"
    return report


def report_handler(ctx):
    from .worker import TaskResult

    with ctx.db_session() as db:
        report = daily_report(db, ctx.tenant_id)
        data = json.dumps(report, indent=2).encode()
        key, sha, size = ctx.artifacts.put(ctx.tenant_id, data)
        art = Artifact(tenant_id=ctx.tenant_id, run_id=ctx.run_id, task_id=ctx.task_id,
                       kind="report", content_type="application/json", size_bytes=size,
                       sha256=sha, storage_key=key, meta={"type": "daily_report"})
        db.add(art)
        db.flush()
        aid = str(art.id)
        db.commit()
    return TaskResult({"artifact_id": aid, "outcomes": len(report["outcomes"]),
                       "blockers": len(report["blockers"]),
                       "next_actions": len(report["next_actions"])})

