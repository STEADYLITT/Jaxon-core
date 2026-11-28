"""Paid ads tasks (ADR-048): drafts first; spend only under an approved Launch.

- ad_draft: claims-check the copy, then create the campaign PAUSED (Meta) / DISABLE (TikTok).
  A draft spends nothing. Saved in ad_campaigns.
- ad_activate: ONLY inside an ACTIVE Launch for the same business whose channels list the
  platform under "ads". The day's budget must fit the launch's daily cap and what's left of
  its total budget. Meta ad sets get the launch's end time, so they stop on their own.
- ad_pause: always allowed (a kill switch). The worker also pauses every live ad whose
  launch is no longer active or has ended (pause_orphans), on every launch sweep.
"""

import uuid

from .worker_errors import NonRetryableError


def draft_handler(ctx):
    from .connectors import ads as A
    from .connectors.claims import check_claims
    from .models import AdCampaign
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    platform = inp.get("platform")
    copy = str(inp.get("copy") or "")
    problems = check_claims(f"{inp.get('name', '')}\n{copy}")
    if problems:
        raise NonRetryableError("the ad fails the claims check: " + "; ".join(problems))
    special = [s for s in inp.get("special_categories") or [] if s in A.SPECIAL]
    budget = int(inp.get("daily_budget_cents") or 0)
    if budget < 100:
        raise NonRetryableError("daily budget must be at least $1.00")
    countries = [str(c).upper()[:2] for c in inp.get("countries") or ["US"]][:25]
    ctx.authorize(Action(Category.DRAFT, f"draft {platform} campaign '{inp.get('name')}' "
                         "(paused, spends nothing)"))
    be = A.backend(platform)
    res = be.draft(str(inp.get("name"))[:200], str(inp.get("objective")), budget, countries,
                   special)
    with ctx.db_session() as db:
        row = AdCampaign(tenant_id=ctx.tenant_id, business_id=ctx.business_id,
                         platform=platform, name=str(inp.get("name"))[:200],
                         objective=str(inp.get("objective")), daily_budget_cents=budget,
                         countries=countries, copy=copy[:2000],
                         external_campaign_id=res["campaign_id"],
                         external_adset_id=res.get("adset_id"),
                         status="mock" if res["status"] == "MOCK" else "draft",
                         run_id=ctx.run_id)
        db.add(row)
        db.flush()
        out = {"ad_campaign_id": str(row.id), "platform": platform, **res,
               "daily_budget_cents": budget}
        db.commit()
    out["summary"] = (f"{platform} campaign drafted (paused, $0 spent)"
                      if res["status"] != "MOCK" else
                      f"[MOCK] {platform} isn't connected: nothing was created")
    return TaskResult(out)


def activate_handler(ctx):
    from sqlalchemy import select

    from .connectors import ads as A
    from .models import AdCampaign, Launch, WorkflowRun
    from .policy import Action, Category
    from .worker import TaskResult

    cid = uuid.UUID(str((ctx.input or {})["ad_campaign_id"]))
    with ctx.db_session() as db:
        c = db.get(AdCampaign, cid)
        if c is None or c.tenant_id != ctx.tenant_id:
            raise NonRetryableError("ad campaign not found")
        if c.status == "mock":
            raise NonRetryableError("this draft is a MOCK: connect the ad account and draft it "
                                    "again")
        launch_id = db.scalar(select(WorkflowRun.launch_id).where(WorkflowRun.id == ctx.run_id))
        la = db.get(Launch, launch_id) if launch_id else None
        if la is None or la.status != "active":
            raise NonRetryableError("ads only go live inside an ACTIVE Launch you approved: "
                                    "approve a Launch whose channels include ads")
        if la.business_id != c.business_id:
            raise NonRetryableError("this ad belongs to a different business than the Launch")
        ch = la.channels or {}
        platforms = ch.get("ad_platforms") or [] if ch.get("ads") else []
        if c.platform not in platforms:
            raise NonRetryableError(f"the Launch doesn't allow {c.platform} ads "
                                    f"(its ads channel lists {platforms or 'nothing'})")
        ids = [x for x in (c.external_campaign_id, c.external_adset_id) if x]
        platform, budget, ends = c.platform, c.daily_budget_cents, la.ends_at
        name = c.name
        db.rollback()
    desc = (f"Turn on {platform} ad '{name[:80]}' at ${budget / 100:.2f}/day until "
            f"{ends:%Y-%m-%d}")
    # Only a Launch can cover this: outside one it would just ask, and we refused above.
    ctx.authorize(Action(Category.SPEND, desc, budget, channel="ads", target=platform))
    reservation = ctx.reserve_budget(budget, desc)
    be = A.backend(platform)
    if platform == "meta" and len(ids) > 1:
        be.set_end_time(ids[1], ends)
    be.set_status(ids, True)
    ctx.settle_spend(reservation, budget, desc)  # today's budget is committed exposure
    with ctx.db_session() as db:
        c = db.get(AdCampaign, cid)
        c.status, c.launch_id = "active", launch_id
        db.commit()
    return TaskResult({"ad_campaign_id": str(cid), "platform": platform, "status": "active",
                       "daily_budget_cents": budget, "ends_at": ends.isoformat(),
                       "summary": desc})


def pause_handler(ctx):
    from .connectors import ads as A
    from .models import AdCampaign
    from .worker import TaskResult

    cid = uuid.UUID(str((ctx.input or {})["ad_campaign_id"]))
    with ctx.db_session() as db:
        c = db.get(AdCampaign, cid)
        if c is None or c.tenant_id != ctx.tenant_id:
            raise NonRetryableError("ad campaign not found")
        ids = [x for x in (c.external_campaign_id, c.external_adset_id) if x]
        platform, was = c.platform, c.status
        db.rollback()
    if was == "active":
        A.backend(platform).set_status(ids, False)
    with ctx.db_session() as db:
        c = db.get(AdCampaign, cid)
        if c.status == "active":
            c.status = "paused"
        db.commit()
    return TaskResult({"ad_campaign_id": str(cid), "status": "paused",
                       "summary": f"{platform} ad paused"})


def pause_orphans(db) -> list[str]:
    """Pause every live ad whose Launch stopped (paused, killed, revoked, expired) or ended.
    Called on every launch sweep by the worker. Returns the paused ad ids."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from .connectors import ads as A
    from .models import AdCampaign, Launch

    now = datetime.now(UTC)
    out = []
    rows = db.execute(select(AdCampaign, Launch).join(Launch, Launch.id == AdCampaign.launch_id)
                      .where(AdCampaign.status == "active")).all()
    for c, la in rows:
        if la.status == "active" and now < la.ends_at:
            continue
        ids = [x for x in (c.external_campaign_id, c.external_adset_id) if x]
        try:
            A.backend(c.platform).set_status(ids, False)
        except Exception:  # noqa: BLE001 - retried on the next sweep
            continue
        c.status = "paused"
        out.append(str(c.id))
    return out
