"""Content + virtual influencer pipeline (spec vol. 03, phase 16, ADR-026).

persona (original, fictional, disclosed) -> generate drafts -> automatic review
-> owner approves -> owner schedules (that click is recorded as the posting approval)
-> worker posts at the scheduled time through the social connector (MOCK until an
Upload-Post key + docs check). Content stays a draft until a person approves it,
enforced by a DB CHECK plus a worker role that can't write approved_by.
"""

import json
import re

from .connectors.claims import check_claims
from .models_ai import ModelRequest
from .worker_errors import NonRetryableError

LIMITS = {  # characters (body + hashtags), hashtags
    "x": (280, 5), "instagram": (2200, 30), "tiktok": (2200, 30), "youtube_shorts": (5000, 15),
    "facebook": (63206, 30), "email": (100_000, 0), "blog": (200_000, 0),
}
AI_DISCLOSURES = ("ai-generated", "#ai", "ai persona", "virtual influencer", "ai character")
AD_DISCLOSURES = ("#ad", "#sponsored", "paid partnership", "advertisement")


def review(item: dict, persona: dict | None) -> dict:
    """Pure compliance review. Returns {"passed": bool, "problems": [...], "checks": {...}}"""
    text = f"{item.get('title', '')}\n{item.get('body', '')}"
    tags = " ".join("#" + h.lstrip("#") for h in item.get("hashtags", []))
    full = f"{text}\n{tags}".lower()
    problems: list[str] = []
    checks = {}

    claims = check_claims(text)
    checks["no_unverified_claims"] = not claims
    problems += claims

    if persona:
        if persona["kind"] == "virtual_influencer" and not persona["fictional"]:
            problems.append("virtual influencers must be original fictional personas")
        disclosed = persona["disclosure"].lower() in full or any(d in full for d in
                                                                 AI_DISCLOSURES)
        checks["ai_disclosure"] = disclosed or persona["kind"] != "virtual_influencer"
        if not checks["ai_disclosure"]:
            problems.append(f"missing AI disclosure (e.g. '{persona['disclosure']}')")
        for name in persona.get("do_not_impersonate", []):
            if re.search(rf"\b(i am|i'm|as|this is)\s+{re.escape(name.lower())}\b", full):
                problems.append(f"impersonates '{name}'")
        checks["no_impersonation"] = not any(p.startswith("impersonates") for p in problems)
        if persona.get("brand_bible", {}).get("real_person_likeness") and not \
                persona["brand_bible"].get("likeness_consent_proof"):
            problems.append("uses a real person's likeness without recorded consent")

    if item.get("sponsored"):
        checks["ad_disclosure"] = any(d in full for d in AD_DISCLOSURES)
        if not checks["ad_disclosure"]:
            problems.append("sponsored content needs '#ad' or 'Paid partnership'")

    max_chars, max_tags = LIMITS.get(item.get("channel"), (100_000, 30))
    length = len(item.get("body", "")) + len(tags)
    checks["within_platform_limits"] = (length <= max_chars
                                        and len(item.get("hashtags", [])) <= max_tags)
    if length > max_chars:
        problems.append(f"{item.get('channel')} limit is {max_chars} characters ({length})")
    if len(item.get("hashtags", [])) > max_tags:
        problems.append(f"{item.get('channel')} allows at most {max_tags} hashtags")

    for a in item.get("assets", []):
        if a.get("rights") not in ("generated", "owned", "licensed"):
            problems.append(f"asset {a.get('ref')} has no cleared rights")
        elif a["rights"] == "licensed" and not a.get("proof"):
            problems.append(f"licensed asset {a.get('ref')} needs proof of license")
    checks["assets_cleared"] = not any("asset" in p for p in problems)
    return {"passed": not problems, "problems": problems, "checks": checks}


GEN_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": ["post", "script", "caption",
                                                            "video_brief", "email"]},
                       "title": {"type": "string"}, "body": {"type": "string"},
                       "hashtags": {"type": "array", "items": {"type": "string"}}},
        "required": ["kind", "title", "body", "hashtags"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


def generate_handler(ctx):
    """Draft content in a persona's voice. Every item is reviewed; nothing is approved."""
    import uuid

    from sqlalchemy import select

    from .models import ContentItem, Persona
    from .worker import TaskResult

    inp = ctx.input or {}
    channel = inp.get("channel", "instagram")
    with ctx.db_session() as db:
        p = db.get(Persona, uuid.UUID(inp["persona_id"])) if inp.get("persona_id") else None
        if p is not None and p.tenant_id != ctx.tenant_id:
            p = None
        persona = ({"id": str(p.id), "name": p.name, "kind": p.kind, "fictional": p.fictional,
                    "bio": p.bio, "brand_bible": p.brand_bible, "disclosure": p.disclosure,
                    "do_not_impersonate": p.do_not_impersonate} if p else None)
        db.rollback()
    if inp.get("persona_id") and persona is None:
        raise NonRetryableError("persona not found")
    rules = (f"Write {inp.get('count', 3)} {channel} content drafts for this brief. "
             "Stay in the persona's voice. Facts only from the brief; no ratings, reviews, "
             "rankings or guaranteed results; no pretending to be a real person. "
             + (f"Every item must include the disclosure '{persona['disclosure']}'."
                if persona and persona["kind"] == "virtual_influencer" else ""))
    resp = ctx.call_model("standard", ModelRequest(
        system=rules, user=json.dumps({"persona": persona, "brief": inp.get("brief", "")}),
        max_tokens=8000, json_schema=GEN_SCHEMA, effort="medium"))
    items = json.loads(resp.text).get("items", [])
    created = []
    with ctx.db_session() as db:
        from .models import WorkflowRun

        launch_id = db.scalar(select(WorkflowRun.launch_id).where(WorkflowRun.id == ctx.run_id))
        for it in items[: int(inp.get("count", 3))]:
            body = it["body"]
            if persona and persona["kind"] == "virtual_influencer" and \
                    persona["disclosure"].lower() not in body.lower():
                body = f"{body}\n\n{persona['disclosure']}"  # always disclose
            row = {"channel": channel, "kind": it["kind"], "title": it["title"][:300],
                   "body": body, "hashtags": it["hashtags"][:30], "sponsored":
                   bool(inp.get("sponsored")), "assets": []}
            rv = review(row, persona)
            c = ContentItem(tenant_id=ctx.tenant_id, business_id=ctx.business_id,
                            persona_id=uuid.UUID(persona["id"]) if persona else None,
                            run_id=ctx.run_id, launch_id=launch_id,
                            status="in_review" if rv["passed"] else "draft",
                            review=rv, **row)
            db.add(c)
            db.flush()
            created.append({"id": str(c.id), "status": c.status, "passed": rv["passed"]})
        db.commit()
    return TaskResult({"mode": resp.mode, "created": created, "channel": channel})


def publish_handler(ctx):
    """Post approved content through the social connector (Upload-Post when configured,
    a labeled MOCK otherwise). The schedule action recorded the owner's posting approval
    (or the content is inside an active Launch approval); policy still checks it here."""
    import uuid

    from sqlalchemy import select

    from .models import ContentItem, Persona, RunEvent
    from .policy import Action, Category
    from .worker import TaskResult

    cid = uuid.UUID((ctx.input or {})["content_id"])
    with ctx.db_session() as db:
        c = db.scalar(select(ContentItem).where(ContentItem.id == cid))
        if c is None or c.tenant_id != ctx.tenant_id:
            raise NonRetryableError("content not found")
        person_approved = c.approved_by is not None and c.status in ("approved", "scheduled")
        # Phase 21: inside a Launch approval, reviewed content may go out without a
        # per-post approval; the policy check below and a DB trigger verify the scope.
        launch_ok = (c.launch_id is not None and c.status in ("in_review", "approved",
                                                              "scheduled")
                     and (c.review or {}).get("passed") is True)
        if not (person_approved or launch_ok):
            raise NonRetryableError(f"content is {c.status}, not approved")
        channel = c.channel
        tags = " ".join("#" + h.lstrip("#") for h in (c.hashtags or []))
        text = f"{c.body}\n\n{tags}".strip()
        assets = list(c.assets or [])
        persona = db.get(Persona, c.persona_id) if c.persona_id else None
        ai_made = bool(persona and persona.kind == "virtual_influencer") or any(
            a.get("rights") == "generated" for a in assets)
        db.rollback()
    ctx.authorize(Action(Category.PUBLIC_POST, f"publish content {cid}", channel="social",
                         target=channel))
    from .connectors.social import social_backend

    sender = social_backend()
    # The same key on every retry of this task: Upload-Post returns the existing job
    # instead of posting twice.
    res = sender.publish(channel, text, assets, idempotency_key=f"content-{cid}",
                         ai_generated=ai_made)
    with ctx.db_session() as db:
        c = db.get(ContentItem, cid)
        c.status, c.external_post_id = "published", res["post_id"][:255]
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="content.published",
                        data={"content_id": str(cid), "channel": channel,
                              "post_id": res["post_id"], "url": res.get("url"),
                              "connector": res["connector"]}))
        db.commit()
    return TaskResult({"content_id": str(cid), "post_id": res["post_id"], "url": res.get("url"),
                       "connector": res["connector"], "channel": channel})


def video_handler(ctx):
    """Generate one short UGC/product clip for a content item (ADR-036). The estimate is a
    SPEND the owner approves (or an active Launch with budget left covers) BEFORE fal.ai is
    called. The clip lands on a NEW content item in review, so posting it needs its own
    approval; the approved original is never changed."""
    import os
    import uuid

    from sqlalchemy import select

    from .connectors import video
    from .models import ContentItem, Persona, RunEvent, WorkflowRun
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    d, ar = int(inp.get("duration_s", 8)), inp.get("aspect_ratio", "9:16")
    res, audio = inp.get("resolution", "1080p"), bool(inp.get("audio", True))
    with ctx.db_session() as db:
        src = db.get(ContentItem, uuid.UUID(inp["content_id"]))
        if src is None or src.tenant_id != ctx.tenant_id:
            raise NonRetryableError("content not found")
        p = db.get(Persona, src.persona_id) if src.persona_id else None
        persona = ({"kind": p.kind, "disclosure": p.disclosure} if p else None)
        row = {"channel": src.channel, "kind": "video", "title": src.title[:300],
               "body": src.body, "hashtags": list(src.hashtags), "sponsored": src.sponsored}
        src_id, persona_id = src.id, src.persona_id
        launch_id = db.scalar(select(WorkflowRun.launch_id).where(WorkflowRun.id == ctx.run_id))
        job = db.scalar(select(RunEvent.data).where(
            RunEvent.run_id == ctx.run_id, RunEvent.type == "video.submitted")
            .order_by(RunEvent.created_at.desc()).limit(1))
        db.rollback()
    prompt = (inp.get("prompt") or f"{row['title']}. {row['body']}")[:4000]
    est = video.estimate_cents(d, res, audio)
    backend = video.video_backend()
    desc = (f"UGC video for '{row['title'][:80]}': {d}s {ar} {res}"
            f"{' with audio' if audio else ''} via {backend.model} - est. ${est / 100:.2f}")
    resuming = bool(job) and job.get("connector") == backend.backend
    if not resuming:  # a resumed job was already approved and reserved (reserve is idempotent)
        ctx.authorize(Action(Category.SPEND, desc, est, channel="ugc_video", always_ask=True))
    reservation = ctx.reserve_budget(est, desc)
    if not resuming:
        job = {**backend.submit(prompt, d, ar, res, audio), "connector": backend.backend}
        with ctx.db_session() as db:
            db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="video.submitted",
                            data=job))
            db.commit()
    url = backend.wait(job, timeout_s=float(os.environ.get("VIDEO_WAIT_SECONDS", "600")))
    paid = est if backend.backend == "fal" else 0
    ctx.settle_spend(reservation, paid, desc)
    asset = {"ref": job["request_id"], "url": url, "kind": "video", "rights": "generated",
             "proof": f"{backend.backend} {backend.model} request {job['request_id']}",
             "mock": backend.backend == "mock"}
    row["assets"] = [asset]
    rv = review(row, persona)
    with ctx.db_session() as db:
        c = ContentItem(tenant_id=ctx.tenant_id, business_id=ctx.business_id,
                        persona_id=persona_id, run_id=ctx.run_id, launch_id=launch_id,
                        status="in_review" if rv["passed"] else "draft", review=rv, **row)
        db.add(c)
        db.flush()
        out = {"content_id": str(c.id), "source_content_id": str(src_id), "video_url": url,
               "cost_cents": paid, "estimate_cents": est, "connector": backend.backend,
               "model": backend.model}
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="video.generated",
                        data=out))
        db.commit()
    return TaskResult(out)
