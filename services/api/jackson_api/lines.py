"""Steady's business lines (ADR-039). Jackson is the CEO of all of them, not a store bot.

Each line is one City department (building) with its own floors, agents, playbook, KPIs,
budget, connectors and launch template. This module is the one registry; it reuses what
exists instead of duplicating it:
- businesses: each business belongs to a line (`businesses.line`); R&D and Finance & Ops are
  cross-line functions with no business of their own;
- agents: built-in task handlers are placed in a line (city.BUILT_IN); missing roles are
  PROPOSED AgentSpecs (status draft) that Steady approves through the agents API;
- launches: unchanged (one approval per launch, per business); each line just names the
  channels and caps its launches usually need;
- KPIs: computed from real rows only. A KPI with no data source says so (value null) —
  it is never estimated.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session


@dataclass
class Line:
    key: str
    name: str
    color: str
    slot: int
    floors: list[tuple[str, str]]
    summary: str
    playbook: list[str]
    rules: list[str]
    keywords: tuple[str, ...]
    connectors: list[str]               # ids in connections.CONNECTORS
    needs_not_built: list[tuple[str, str]] = field(default_factory=list)  # (variable, why)
    launch: dict = field(default_factory=dict)  # suggested scope for a Launch approval
    budget_monthly_cents: int = 0
    business: tuple[str, str, str] | None = None  # (seed source_id, name, kind) if its own
    proposed: list[dict] = field(default_factory=list)  # AgentSpecs to propose (draft)


def _spec(name, role, objective, instructions):
    return {"name": name, "role": role, "objective": objective, "instructions": instructions}


LINES: list[Line] = [
    Line("rnd", "R&D / New Ideas", "#4dd2ff", 0,
         [("ideas", "Opportunity Board"), ("simulate", "Simulation")],
         "Finds opportunities across every line: trends, apps, digital products, e-com "
         "products, agency clients, content channels, deals.",
         ["Collect signals from permitted sources (each with its URL).",
          "Score them on the opportunity board (demand, margin, effort, risk, fit).",
          "Simulate the best ones (economics, not live money).",
          "Propose a Launch for the winner: one approval with scope and budget."],
         ["Every claim cites a source URL.", "No live trading: simulation only."],
         ("idea", "opportunit", "trend", "find new", "new line", "deal", "research"),
         ["models"], [],
         {"channels": {"shopify_publish": False, "social_accounts": [], "email_lists": [],
                       "ads": False}, "note": "R&D proposes launches for the other lines"},
         budget_monthly_cents=2_000,
         proposed=[_spec("trend-scout", "research: cross-line trend and opportunity scout",
                         "Turn the sources Steady gives into scored opportunities for any line",
                         "Read only the sources provided. Cite each signal's URL. Score "
                         "demand, margin, effort, risk and fit. Never invent numbers.")]),
    Line("stu", "Steady Trades University", "#ff3df2", 1,
         [("content", "Content"), ("email", "Email & Funnels"), ("ads", "Paid Ads")],
         "Markets Replay Trainer ($19.99/mo), One-on-One, Self-Paced, Live Group and the "
         "indicators: content, emails, ads, funnels and follow-up.",
         ["Plan the week's content per offer.", "Draft posts and emails (reviewed for claims).",
          "Steady approves, or a Launch covers the channel.",
          "Measure real sales from Whop only."],
         ["No income promises or guaranteed results (claims check runs on every draft).",
          "Revenue counts only when Whop reports it."],
         ("replay trainer", "steady trades", "trades university", "course", "one-on-one",
          "self-paced", "live group", "indicator", "student", "trading education"),
         ["models", "social", "email"],
         [("WHOP_API_KEY", "real Replay Trainer / course sales (revenue only from Whop)")],
         {"channels": {"shopify_publish": False, "social_accounts": ["instagram", "tiktok",
                                                                    "x"],
                       "email_lists": ["students"], "ads": False}},
         budget_monthly_cents=10_000,
         business=("steady-trades-university", "Steady Trades University", "education"),
         proposed=[_spec("stu-funnel-writer", "marketing: course funnel and email writer",
                         "Write funnel pages and email sequences for Steady Trades University "
                         "offers", "Educational tone. No income claims or guarantees. Every "
                         "email has the unsubscribe link and postal address.")]),
    Line("agency", "Agency", "#7cff6b", 2,
         [("clients", "Client Accounts"), ("delivery", "Deliverables"), ("web", "Sites")],
         "Markets other companies: client records, deliverables (content, ads, sites, "
         "outreach), schedules, client reports and a read-only client City link.",
         ["Keep each client as a record with its scope and schedule.",
          "Produce deliverables; each one is approved before it goes out.",
          "Send a weekly client report from real numbers.",
          "Share a read-only City link with the client."],
         ["Client work only inside the client's agreed scope.",
          "Reports use real data; missing data is shown as missing."],
         ("client", "agency", "deliverable", "client report", "retainer"),
         ["models", "social", "email", "hosting"], [],
         {"channels": {"shopify_publish": False, "social_accounts": [], "email_lists": [],
                       "ads": False}, "note": "one launch per client engagement"},
         budget_monthly_cents=10_000,
         business=("steady-ventures", "Steady Ventures", "agency"),
         proposed=[_spec("client-reporter", "agency: weekly client report writer",
                         "Write each client's weekly report from their real numbers",
                         "Only numbers from the records given. Say plainly what is not "
                         "measured yet. No promises.")]),
    Line("clips", "Clip Farming", "#ffb020", 3,
         [("scouting", "Scouting"), ("editing", "Cutting & Captions"), ("posting", "Posting")],
         "Finds clippable moments from creators and streams (e.g. GTA 6), cuts and captions "
         "them, posts to TikTok/Shorts/Reels via Upload-Post, tracks views and payouts.",
         ["Pick creators who allow clipping (clipping programs/permission).",
          "Find moments from their VODs/streams.", "Cut + caption, credit the creator.",
          "Post through Upload-Post (approval or Launch).", "Track views and payouts."],
         ["Only creators whose terms allow clipping; always credit the source.",
          "No reposting of full content."],
         ("clip", "clipping", "gta", "stream highlight", "vod", "highlight"),
         ["social", "video"],
         [("YOUTUBE_API_KEY", "find clippable moments on YouTube VODs"),
          ("TWITCH_CLIENT_ID", "find clippable moments on Twitch streams"),
          ("CLIP_PAYOUTS_SOURCE", "real views/payouts (clipping program dashboard)")],
         {"channels": {"shopify_publish": False,
                       "social_accounts": ["tiktok", "youtube_shorts", "instagram"],
                       "email_lists": [], "ads": False}},
         budget_monthly_cents=5_000,
         business=("clip-farming", "Clip Farming", "clip_farming"),
         proposed=[_spec("clip-scout", "clips: find clippable moments",
                         "Find clippable moments from permitted creators' VODs and streams",
                         "Only creators whose terms allow clipping. Return timestamps and "
                         "why each moment works. Credit the creator."),
                   _spec("clip-captioner", "clips: cut list and captions",
                         "Write cut lists and captions for approved moments",
                         "Short hook, credit line with the creator's handle, no clickbait "
                         "lies.")]),
    Line("creators", "Creator Agency", "#ff8fa3", 4,
         [("calendar", "Content Calendar"), ("messages", "Captions & DMs"),
          ("earnings", "Earnings")],
         "Manages real creators who signed with Steady: content calendars, SFW promo posts, "
         "caption/DM drafts that wait for approval, earnings tracking.",
         ["Plan each creator's week (SFW promo only).", "Draft captions and DMs.",
          "Everything waits for the creator's/Steady's approval or a Launch scope.",
          "Track earnings from the real payout source."],
         ["Agents never generate explicit content.",
          "Nothing is posted or sent without an approval or a Launch that covers it.",
          "Only for creators who signed with Steady."],
         ("creator", "talent", "signed creator", "creator x", "dm"),
         ["social", "email"],
         [("CREATOR_EARNINGS_SOURCE", "real earnings per creator (platform payout reports)")],
         {"channels": {"shopify_publish": False, "social_accounts": ["instagram", "x"],
                       "email_lists": [], "ads": False}, "note": "one launch per creator"},
         budget_monthly_cents=3_000,
         business=("creator-agency", "Creator Agency", "creator_agency"),
         proposed=[_spec("creator-planner", "creators: weekly SFW content calendar",
                         "Plan a signed creator's week of SFW promo posts",
                         "SFW only. Drafts wait for approval. Never write explicit content."),
                   _spec("caption-drafter", "creators: caption and DM drafts",
                         "Draft captions and DMs for a creator's approved calendar",
                         "SFW only. Drafts only: they wait for approval before anything "
                         "is posted or sent.")]),
    Line("influencers", "AI Influencers", "#8f7bff", 5,
         [("personas", "Personas"), ("studio", "Image & Video"), ("growth", "Growth")],
         "SFW brand/lifestyle AI accounts, clearly labeled as AI per platform rules: content "
         "plans, image/video via fal.ai, posting via Upload-Post, growth tracking.",
         ["Define the persona (fictional, labeled as AI).",
          "Plan content; generate images/video (cost approved first).",
          "Post with the AI label (is_ai_generated) via Upload-Post.", "Track growth."],
         ["Always labeled as AI; never impersonates a real person.", "SFW only."],
         ("ai influencer", "virtual model", "ai model", "persona", "ai account"),
         ["video", "social"],
         [("PLATFORM_ANALYTICS", "real follower growth (platform analytics)")],
         {"channels": {"shopify_publish": False,
                       "social_accounts": ["instagram", "tiktok"], "email_lists": [],
                       "ads": False}, "note": "one launch per persona"},
         budget_monthly_cents=5_000,
         business=("ai-influencers", "AI Influencers", "ai_influencer"),
         proposed=[_spec("persona-planner", "influencers: AI persona content planner",
                         "Plan a labeled AI persona's posts and generation prompts",
                         "SFW lifestyle/brand only. Always disclose it is AI. Never resemble "
                         "a real person.")]),
    Line("ecommerce", "E-commerce", "#00e5ff", 6,
         [("research", "Product Research"), ("store", "Store")],
         "Shopify stores, product research and listings (drafts until a Launch covers "
         "publishing).",
         ["Research products from sources.", "Model the economics.",
          "Create draft listings and verify them.", "Publish only inside a Launch."],
         ["Drafts until a Launch approval covers publishing."],
         ("product", "shopify", "store", "listing", "e-com", "ecom", "dropship"),
         ["shopify"], [],
         {"channels": {"shopify_publish": True, "social_accounts": [], "email_lists": [],
                       "ads": False}},
         budget_monthly_cents=5_000,
         business=("shopify-store", "Shopify Store", "ecommerce"),
         proposed=[_spec("product-researcher", "ecommerce: product researcher",
                         "Find product ideas within Steady's price and margin limits",
                         "Cite sources. Compute landed cost and margin. Respect the price "
                         "cap in the command.")]),
    Line("finance", "Finance & Ops", "#ffd166", 7,
         [("briefing", "CEO Briefing"), ("ops", "Ops & Agents")],
         "Spend, revenue by line, cost caps and the daily CEO briefing across all lines.",
         ["Track spend per line and agent.", "Record real revenue by line and source.",
          "Enforce cost caps (cost guard).", "Write Steady's daily briefing across lines."],
         ["Revenue is actual only when a connected source or a referenced entry reports it."],
         ("spend", "revenue", "budget", "briefing", "finance", "cost", "profit", "report",
          "summary", "recap"),
         ["models"], [],
         {}, budget_monthly_cents=0),
]
BY_KEY = {ln.key: ln for ln in LINES}

# Seeded businesses without a line of their own map here (owner can change it).
BUSINESS_LINE_BY_SOURCE = {"csw-trades": "stu", "apex-7": "rnd"}


def line_of_text(text: str) -> tuple[Line | None, list[str]]:
    """Which line a command is about, and the words that showed it."""
    low = text.lower()
    best, hits = None, []
    for ln in LINES:
        h = [w for w in ln.keywords if w in low]
        # more matching words wins; on a tie a specific line beats the cross-line ones
        better = len(h) > len(hits) or (h and len(h) == len(hits) and best is not None
                                        and best.key in ("rnd", "finance"))
        if better:
            best, hits = ln, h
    return best, hits


# --- KPIs: real rows only -----------------------------------------------------------------

def _kpi(name, value, unit="", source="", note=None):
    return {"name": name, "value": value, "unit": unit, "source": source,
            "note": note if value is None else None}


def kpis(db: Session, tenant_id, key: str) -> list[dict]:
    from .models import (
        Agent,
        Business,
        Contact,
        ContentItem,
        Event,
        LedgerEntry,
        ModelUsage,
        Opportunity,
        OutreachCampaign,
        OutreachMessage,
        Persona,
        RevenueEntry,
        ShopProduct,
    )

    now = datetime.now(UTC)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    biz = select(Business.id).where(Business.tenant_id == tenant_id, Business.line == key)

    def count(q):
        return int(db.scalar(q) or 0)

    def revenue(source: str):
        has = count(select(func.count()).select_from(RevenueEntry).where(
            RevenueEntry.tenant_id == tenant_id, RevenueEntry.source == source))
        if not has:
            return None
        return int(db.scalar(select(func.coalesce(func.sum(RevenueEntry.amount_cents), 0))
                             .where(RevenueEntry.tenant_id == tenant_id,
                                    RevenueEntry.source == source,
                                    RevenueEntry.kind == "actual",
                                    RevenueEntry.business_id.in_(biz),
                                    RevenueEntry.created_at >= month)) or 0) / 100

    def posts(statuses=("published",), channels=None, since=month):
        q = select(func.count()).select_from(ContentItem).where(
            ContentItem.tenant_id == tenant_id, ContentItem.business_id.in_(biz),
            ContentItem.status.in_(statuses), ContentItem.updated_at >= since)
        if channels:
            q = q.where(ContentItem.channel.in_(channels))
        return count(q)

    if key == "rnd":
        return [
            _kpi("New opportunities (7 days)", count(select(func.count()).select_from(
                Opportunity).where(Opportunity.tenant_id == tenant_id,
                                   Opportunity.created_at >= week)), "", "opportunities"),
            _kpi("Average score", round(float(db.scalar(select(func.avg(Opportunity.score))
                 .where(Opportunity.tenant_id == tenant_id)) or 0), 1) if count(
                select(func.count()).select_from(Opportunity).where(
                    Opportunity.tenant_id == tenant_id)) else None, "/100", "opportunities",
                 "no opportunities scored yet"),
        ]
    if key == "stu":
        return [
            _kpi("Revenue this month (Whop)", revenue("whop"), "USD", "revenue_entries",
                 "Whop isn't connected yet: no revenue is shown until it reports"),
            _kpi("Posts published this month", posts(), "", "content_items"),
            _kpi("Emails sent this month", count(select(func.count()).select_from(
                OutreachMessage).where(
                OutreachMessage.tenant_id == tenant_id, OutreachMessage.status == "sent",
                OutreachMessage.sent_at >= month,
                OutreachMessage.campaign_id.in_(select(OutreachCampaign.id).where(
                    OutreachCampaign.business_id.in_(biz))))), "", "outreach_messages"),
        ]
    if key == "agency":
        return [
            _kpi("Active clients", count(select(func.count()).select_from(Contact).where(
                Contact.tenant_id == tenant_id, Contact.business_id.in_(biz),
                Contact.stage == "won")), "", "contacts (stage won)"),
            _kpi("Deliverables published this month", posts(), "", "content_items"),
            _kpi("Open proposals", count(select(func.count()).select_from(Contact).where(
                Contact.tenant_id == tenant_id, Contact.business_id.in_(biz),
                Contact.stage == "proposal")), "", "contacts (stage proposal)"),
        ]
    if key == "clips":
        return [
            _kpi("Clips posted this month", posts(channels=("tiktok", "youtube_shorts",
                                                             "instagram")), "", "content_items"),
            _kpi("Views", None, "", "", "needs a views source (platform analytics)"),
            _kpi("Payouts", None, "USD", "", "needs CLIP_PAYOUTS_SOURCE"),
        ]
    if key == "creators":
        return [
            _kpi("Drafts waiting for approval", posts(("in_review",), since=month - timedelta(
                days=365)), "", "content_items"),
            _kpi("Posts published this month", posts(), "", "content_items"),
            _kpi("Creator earnings", None, "USD", "", "needs CREATOR_EARNINGS_SOURCE"),
        ]
    if key == "influencers":
        return [
            _kpi("AI personas", count(select(func.count()).select_from(Persona).where(
                Persona.tenant_id == tenant_id, Persona.kind == "virtual_influencer")),
                 "", "personas"),
            _kpi("Posts published this month", posts(), "", "content_items"),
            _kpi("Followers gained", None, "", "", "needs PLATFORM_ANALYTICS"),
        ]
    if key == "ecommerce":
        return [
            _kpi("Draft products", count(select(func.count()).select_from(ShopProduct).where(
                ShopProduct.tenant_id == tenant_id, ShopProduct.status == "DRAFT")),
                 "", "shop_products"),
            _kpi("Published products", count(select(func.count()).select_from(ShopProduct)
                 .where(ShopProduct.tenant_id == tenant_id, ShopProduct.status == "ACTIVE")),
                 "", "shop_products"),
            _kpi("Revenue this month (Shopify)", revenue("shopify"), "USD", "revenue_entries",
                 "no Shopify sales reported yet"),
        ]
    if key == "finance":
        model = int(db.scalar(select(func.coalesce(func.sum(ModelUsage.cost_usd_micros), 0))
                              .where(ModelUsage.tenant_id == tenant_id,
                                     ModelUsage.created_at >= month)) or 0) / 1e6
        ledger = int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
                               .where(LedgerEntry.tenant_id == tenant_id,
                                      LedgerEntry.kind == "commit",
                                      LedgerEntry.created_at >= month)) or 0) / 100
        actual = int(db.scalar(select(func.coalesce(func.sum(RevenueEntry.amount_cents), 0))
                               .where(RevenueEntry.tenant_id == tenant_id,
                                      RevenueEntry.kind == "actual",
                                      RevenueEntry.created_at >= month)) or 0) / 100
        paused = count(select(func.count()).select_from(Event).where(
            Event.tenant_id == tenant_id, Event.type == "alert.raised", Event.ts >= week))
        agents = count(select(func.count()).select_from(Agent).where(
            Agent.tenant_id == tenant_id))
        return [_kpi("Spend this month", round(model + ledger, 2), "USD",
                     "model_usage + ledger"),
                _kpi("Actual revenue this month (all lines)", actual, "USD", "revenue_entries"),
                _kpi("Alerts (7 days)", paused, "", "events"),
                _kpi("Agents on staff", agents, "", "agents")]
    return []
