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
        if not c.external_ad_id:
            raise NonRetryableError("add the ad (creative) first: a campaign with no ad "
                                    "serves nothing")
        if la.business_id != c.business_id:
            raise NonRetryableError("this ad belongs to a different business than the Launch")
        ch = la.channels or {}
        platforms = ch.get("ad_platforms") or [] if ch.get("ads") else []
        if c.platform not in platforms:
            raise NonRetryableError(f"the Launch doesn't allow {c.platform} ads "
                                    f"(its ads channel lists {platforms or 'nothing'})")
        ids = _ids(c)
        platform, budget, ends = c.platform, c.daily_budget_cents, la.ends_at
        name = c.name
        db.rollback()
    desc = (f"Turn on {platform} ad '{name[:80]}' at ${budget / 100:.2f}/day until "
            f"{ends:%Y-%m-%d}")
    # Only a Launch can cover this: outside one it would just ask, and we refused above.
    # The launch's daily cap must hold every live ad's daily budget together, and the
    # budget left must hold at least one more day. What's actually spent is what the
    # platforms report (sync_insights): it counts against the launch budget as it happens.
    ctx.authorize(Action(Category.SPEND, desc, budget, channel="ads", target=platform))
    with ctx.db_session() as db:
        from sqlalchemy import func

        from .launches import metrics
        live = int(db.scalar(select(func.coalesce(func.sum(AdCampaign.daily_budget_cents), 0))
                             .where(AdCampaign.launch_id == launch_id,
                                    AdCampaign.status == "active",
                                    AdCampaign.id != cid)) or 0)
        left = metrics(db, db.get(Launch, launch_id))["budget_left_cents"]
        cap = db.get(Launch, launch_id).daily_cap_cents
        db.rollback()
    if live + budget > cap:
        raise NonRetryableError(f"live ads would spend ${(live + budget) / 100:.2f}/day, over "
                                f"the launch's daily cap of ${cap / 100:.2f}")
    if budget > left:
        raise NonRetryableError(f"only ${left / 100:.2f} of the launch budget is left")
    be = A.backend(platform)
    if platform == "meta" and len(ids) > 1:
        be.set_end_time(ids[1], ends)
    be.set_status(ids, True)
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
        ids = _ids(c)
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
        ids = _ids(c)
        try:
            A.backend(c.platform).set_status(ids, False)
        except Exception:  # noqa: BLE001 - retried on the next sweep
            continue
        c.status = "paused"
        out.append(str(c.id))
    return out


# --- Creatives and insights (ADR-051) ----------------------------------------------------------

def _https(u) -> str:
    u = str(u or "").strip()
    if u and not u.startswith("https://"):
        raise NonRetryableError(f"{u[:60]} must be an https:// link")
    return u


def creative_handler(ctx):
    """Add the creative and the ad to a draft, still paused. Copy is claims-checked."""
    from .connectors import ads as A
    from .connectors.claims import check_claims
    from .models import AdCampaign, ContentItem
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    cid = uuid.UUID(str(inp["ad_campaign_id"]))
    with ctx.db_session() as db:
        c = db.get(AdCampaign, cid)
        if c is None or c.tenant_id != ctx.tenant_id:
            raise NonRetryableError("ad campaign not found")
        if c.status == "mock":
            raise NonRetryableError("this draft is a MOCK: connect the ad account and draft it "
                                    "again")
        if c.external_ad_id:
            raise NonRetryableError("this campaign already has its ad")
        video = inp.get("video_url")
        text = str(inp.get("text") or c.copy or "")
        if inp.get("content_id"):  # an approved content draft supplies the video and text
            item = db.get(ContentItem, uuid.UUID(str(inp["content_id"])))
            if item is None or item.tenant_id != ctx.tenant_id:
                raise NonRetryableError("content not found")
            if item.status not in ("approved", "published"):
                raise NonRetryableError("approve the content draft before using it in an ad")
            vids = [a["url"] for a in item.assets or [] if a.get("kind") == "video"
                    and str(a.get("url", "")).startswith("https://")]
            video = video or (vids[0] if vids else None)
            text = text or item.body
        platform, adset, name = c.platform, c.external_adset_id, c.name
        db.rollback()
    cr = {"image_url": _https(inp.get("image_url")), "video_url": _https(video),
          "thumbnail_url": _https(inp.get("thumbnail_url")), "link": _https(inp.get("link")),
          "headline": str(inp.get("headline") or name)[:100], "text": text[:2000],
          "description": str(inp.get("description") or "")[:200],
          "cta": inp.get("cta") or "LEARN_MORE"}
    if not cr["link"]:
        raise NonRetryableError("an ad needs the https link it sends people to")
    if cr["cta"] not in A.CTAS:
        raise NonRetryableError(f"call to action must be one of {A.CTAS}")
    if not (cr["image_url"] or cr["video_url"]):
        raise NonRetryableError("give an image_url or a video (video_url / an approved "
                                "content draft with a video)")
    if cr["video_url"] and not cr["thumbnail_url"]:
        raise NonRetryableError("a video ad needs a thumbnail_url (its cover image)")
    problems = check_claims(f"{cr['headline']}\n{cr['text']}\n{cr['description']}")
    if problems:
        raise NonRetryableError("the ad fails the claims check: " + "; ".join(problems))
    ctx.authorize(Action(Category.DRAFT, f"add the ad to {platform} campaign '{name[:80]}' "
                         "(paused, spends nothing)"))
    res = A.backend(platform).add_ad(name, adset, cr)
    with ctx.db_session() as db:
        c = db.get(AdCampaign, cid)
        c.external_ad_id, c.creative = res["ad_id"], {**cr, **res}
        db.commit()
    return TaskResult({"ad_campaign_id": str(cid), **res, "status": "paused",
                       "summary": f"{platform} ad created (paused): {cr['headline'][:60]}"})


def refresh(db, c, backend=None) -> dict:
    from datetime import UTC, datetime

    from .connectors import ads as A

    m = (backend or A.backend(c.platform)).insights(c.external_campaign_id)
    c.spend_cents_reported, c.impressions = m["spend_cents"], m["impressions"]
    c.clicks, c.results, c.insights_at = m["clicks"], m["results"], datetime.now(UTC)
    return m


def insights_handler(ctx):
    from .models import AdCampaign
    from .worker import TaskResult

    cid = uuid.UUID(str((ctx.input or {})["ad_campaign_id"]))
    with ctx.db_session() as db:
        c = db.get(AdCampaign, cid)
        if c is None or c.tenant_id != ctx.tenant_id:
            raise NonRetryableError("ad campaign not found")
        if c.status in ("mock", "draft") and not c.launch_id:
            raise NonRetryableError("this ad has never run: there's nothing to report")
        m = refresh(db, c)
        db.commit()
    return TaskResult({"ad_campaign_id": str(cid), **m,
                       "summary": f"Reported: ${m['spend_cents'] / 100:.2f} spent, "
                                  f"{m['results']} {m['result_name']}"})


def sync_insights(db, every_s: int = 1800) -> list[str]:
    """On every launch sweep: refresh live ads' reported spend and results (at most every
    30 minutes each), and pause a Launch's ads once REPORTED spend reaches its total budget.
    Returns the ads paused for budget."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from .connectors import ads as A
    from .models import AdCampaign, Launch

    now = datetime.now(UTC)
    for c in db.scalars(select(AdCampaign).where(AdCampaign.status == "active")).all():
        if c.insights_at and c.insights_at > now - timedelta(seconds=every_s):
            continue
        try:
            refresh(db, c)
        except Exception as e:  # noqa: BLE001 - tried again on the next sweep
            import logging
            logging.getLogger(__name__).warning("ad insights for %s failed: %s", c.id, e)
            continue
    db.flush()
    paused = []
    rows = db.execute(select(AdCampaign.launch_id,
                             func.coalesce(func.sum(AdCampaign.spend_cents_reported), 0))
                      .where(AdCampaign.launch_id.is_not(None))
                      .group_by(AdCampaign.launch_id)).all()
    for lid, spent in rows:
        la = db.get(Launch, lid)
        if la is None or int(spent) < la.total_budget_cents:
            continue
        for c in db.scalars(select(AdCampaign).where(AdCampaign.launch_id == lid,
                                                     AdCampaign.status == "active")).all():
            try:
                A.backend(c.platform).set_status(_ids(c), False)
            except Exception:  # noqa: BLE001
                continue
            c.status = "paused"
            paused.append(str(c.id))
    return paused


def _ids(c) -> list[str]:
    return [x for x in (c.external_campaign_id, c.external_adset_id, c.external_ad_id) if x]
