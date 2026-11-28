"""Steady's departments (ADR-039, expanded to the full Jackson scope).

Jackson Vale is an AI CEO that discovers, validates, builds and operates businesses of any
kind, then markets every company Steady owns (spec vol. 00, 03). Every department follows
the same core loop: discover -> evidence -> unit economics -> propose an experiment (a
Launch with scope + budget) -> Steady approves once -> build in a sandbox -> QA -> publish
inside the scope -> market -> measure -> iterate or kill -> write the lesson to memory.

Each department is one City building with floors, agents, a playbook, KPIs, a budget,
connectors and launch rules. This registry reuses what exists:
- businesses: each belongs to the department that builds/operates it (`businesses.line`);
- agents: built-in handlers are placed here (city.BUILT_IN); missing roles are PROPOSED
  AgentSpecs (drafts, empty tool scope = level 1: drafts only) that Steady approves;
- launches: one approval per launch; restricted actions (vol. 04) are never covered;
- KPIs: real rows only; no data source -> null with the reason, never estimated.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

MAX_PROPOSED_AGENTS = 40  # vol. 05: cap total agents; Jackson proposes, Steady approves


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
    launch: dict = field(default_factory=dict)
    restricted: list[str] = field(default_factory=list)  # never inside a Launch (vol. 04)
    budget_monthly_cents: int = 0
    proposed: list[dict] = field(default_factory=list)  # AgentSpecs to propose (drafts)


def _spec(name, role, objective, instructions):
    return {"name": name, "role": role, "objective": objective, "instructions": instructions}


def _ch(social=(), email=(), shopify=False, ads=False, note=None):
    out = {"channels": {"shopify_publish": shopify, "social_accounts": list(social),
                        "email_lists": list(email), "ads": ads}}
    if note:
        out["note"] = note
    return out


CORE_LOOP = ["Discover and gather evidence (sources cited).", "Work out the unit economics.",
             "Propose an experiment: one Launch with scope and budget.",
             "Steady approves once.", "Build in a sandbox, then QA.",
             "Publish only inside the approved scope.", "Market, measure.",
             "Iterate or kill; write the lesson to memory with sources."]

LINES: list[Line] = [
    Line("rnd", "R&D / Opportunities", "#4dd2ff", 0,
         [("signals", "Signals"), ("board", "Opportunity Board"), ("simulate", "Simulation")],
         "Finds trends across every department: courses, apps/SaaS, products, content "
         "channels, agency niches, deals. Scored, sourced, deduplicated; Simulate / Plan / "
         "Propose Launch.",
         ["Collect signals from permitted sources (each with its URL).",
          "Deduplicate and score: demand, saturation, margin, effort, risk, fit.",
          "Simulate the best (economics, no live money).",
          "Propose a Launch for the department that would run it."],
         ["Every claim cites a source URL.",
          "Authorized data access only; no promise of unrestricted scraping."],
         ("idea", "opportunit", "trend", "find new", "deal", "niche"),
         ["models", "research"], [], _ch(note="R&D proposes launches for the other departments"),
         budget_monthly_cents=2_000,
         proposed=[_spec("trend-scout", "research: cross-department trend scout",
                         "Turn the sources Steady gives into scored, deduplicated "
                         "opportunities for any department",
                         "Read only the sources provided. Cite each signal's URL. Never "
                         "invent numbers.")]),
    Line("courses", "Course & Education Factory", "#ff3df2", 1,
         [("design", "Course Design"), ("lessons", "Lessons & Media"),
          ("platform", "Student Platform"), ("launch", "Sales & Launch")],
         "From a trending topic: a full course (outline, lessons, scripts, slides/cards, "
         "quizzes, worksheets, AI video/voice where approved), a student platform (login, "
         "dashboard, progress, quizzes, certificates, community, Whop payments), then a "
         "sales page, funnel, email sequence and ads plan.",
         [*CORE_LOOP, "Course pipeline: outline -> lessons -> quizzes/worksheets -> student "
          "platform draft -> sales page + funnel + emails -> Launch proposal."],
         ["Education only: no income promises or guaranteed results.",
          "Payments only through Whop; the platform is a draft until a Launch covers it.",
          "AI video/voice only where Steady approved it, labeled as AI."],
         ("course", "lesson", "curriculum", "student platform", "academy", "university",
          "blueprint", "education", "teach"),
         ["models", "video", "email", "whop", "voice", "hosting"],
         [],
         _ch(social=("instagram", "tiktok", "youtube_shorts"), email=("course-leads",)),
         budget_monthly_cents=15_000,
         proposed=[_spec("course-architect", "courses: course outline and curriculum",
                         "Design a course from a validated topic: outcomes, modules, lessons",
                         "Teach, don't promise income. Each module has a measurable outcome."),
                   _spec("lesson-writer", "courses: lesson scripts, slides, quizzes",
                         "Write lesson scripts, slide/picture-card text, quizzes and "
                         "worksheets for an approved outline",
                         "Accurate, plain language. No guaranteed results.")]),
    Line("marketing", "Marketing (all companies)", "#ff8fa3", 2,
         [("content", "Content"), ("email", "Email & Funnels"), ("ads", "Ads"),
          ("seo", "SEO"), ("calendar", "Brand Calendars")],
         "Markets every Steady company: Steady Trades University (Replay Trainer, "
         "One-on-One, Self-Paced, Live Group, indicators incl. PRO Breakout), CSW Trades, "
         "Steady Ventures, Atom Labs, Steady Growth OS, SHRED 90 and client brands. "
         "Content, clips, emails, funnels, ads, SEO; one calendar per brand.",
         ["One calendar per brand.", "Draft content and emails (claims-checked).",
          "Approval or a Launch that names the channel.", "Measure; repurpose what works."],
         ["No income promises or fake testimonials.",
          "Every email has unsubscribe + postal address; opt-outs are permanent."],
         ("marketing", "post", "posts", "campaign", "funnel", "seo", "ads", "replay trainer",
          "steady trades", "pro breakout", "atom labs", "growth os", "shred 90",
          "csw trades", "brand"),
         ["models", "social", "email", "whop", "voice"],
         [],
         _ch(social=("instagram", "tiktok", "x", "facebook"), email=("students",)),
         budget_monthly_cents=15_000,
         proposed=[_spec("funnel-writer", "marketing: funnels and email sequences",
                         "Write funnel pages and email sequences for any Steady brand",
                         "Brand voice per company. No income claims. Unsubscribe + address "
                         "in every email.")]),
    Line("agency", "Agency", "#7cff6b", 3,
         [("clients", "Client Accounts"), ("delivery", "Deliverables"),
          ("reports", "Client Reports")],
         "Markets other companies: client CRM, proposals, deliverables (content, ads, "
         "sites, automations, AI lead-gen systems), schedules, reports, client-view City.",
         ["Client record + scope.", "Deliverables, each approved before it goes out.",
          "Weekly client report from real numbers.", "Read-only client City link."],
         ["Client work only inside the agreed scope, in the client's own workspace.",
          "Reports show missing data as missing."],
         ("client", "agency", "deliverable", "client report", "retainer"),
         ["models", "social", "email"], [], _ch(note="one launch per client engagement"),
         budget_monthly_cents=10_000,
         proposed=[_spec("client-reporter", "agency: weekly client report writer",
                         "Write each client's weekly report from their real numbers",
                         "Only numbers from the records given; say what is not measured.")]),
    Line("sales", "Sales & Lead Gen", "#00e5ff", 4,
         [("leads", "Lead Research"), ("outreach", "Outreach"), ("calls", "Call Desk"),
          ("pipeline", "Pipeline")],
         "Lawful lead research, scoring, outreach drafts (opt-outs respected), call lists "
         "and scripts for the sales team, booked calls, proposals, pipeline (insurance "
         "lead gen/RGE, AI automation, local businesses).",
         ["Find leads from permitted sources with a lawful basis.", "Score and dedupe.",
          "Draft outreach; send only after approval or inside a Launch.",
          "Call lists + scripts for the team; respect quiet hours and consent.",
          "Book calls, send proposals, track stages."],
         ["Opt-outs are permanent; SMS/calls need recorded consent.",
          "Humanize for clarity, never to deceive."],
         ("lead", "leads", "prospect", "call list", "script", "pipeline", "proposal",
          "insurance", "rge", "local business", "outreach", "sales"),
         ["models", "email", "sms", "research"], [],
         _ch(email=("prospects",), note="each campaign names its list"),
         budget_monthly_cents=5_000,
         proposed=[_spec("lead-researcher", "sales: lawful lead research and scoring",
                         "Find and score leads from permitted sources for a named niche",
                         "Permitted sources only, lawful basis recorded, dedupe, no "
                         "scraping behind logins."),
                   _spec("call-scripter", "sales: call lists and scripts",
                         "Write call lists and scripts for the sales team",
                         "Consent and quiet hours respected; honest, no pressure tactics.")]),
    Line("factory", "App / SaaS / Web Factory", "#8f7bff", 5,
         [("specs", "Requirements & Wireframes"), ("build", "Build & Tests"),
          ("preview", "Preview & QA"), ("deploy", "Deploy & Domains")],
         "From an idea: requirements, wireframes, repo, build, tests, preview link, "
         "approval, deploy (e.g. Chart Lingo, Candle Rush, CartChef, InkFlow/GOTTINK CRM, a "
         "wholesaling app, a sports-data app, client sites and landing pages). Tracks "
         "sites, domains and hosting.",
         ["Requirements + wireframes from the description.", "Build from templates.",
          "Automated tests + accessibility + preview link.",
          "Publish only under a Launch or an approval.", "Track domain and hosting."],
         ["Domain ownership verified before launch; rollback ready.",
          "Privacy notice and payments checked before going live."],
         ("website", "landing page", "site", "app", "saas", "web app", "build a page",
          "wireframe", "gottink", "inkflow", "chart lingo", "candle rush", "cartchef",
          "domain"),
         ["models", "dns", "hosting"],
         [("GITHUB_APP_TOKEN", "create repos for new apps (not built yet)")],
         _ch(note="publishing a site/app is a production change: approval or Launch"),
         budget_monthly_cents=5_000,
         proposed=[_spec("web-developer", "factory: requirements to site/app",
                         "Turn a description into requirements, wireframes and a site/app "
                         "build plan", "Reusable templates; accessible; nothing goes live "
                         "without approval."),
                   _spec("qa-tester", "factory: preview QA",
                         "Check previews: links, layout, accessibility, forms",
                         "Report real failures; never mark a failure as passed.")]),
    Line("clips", "Content & Clip Farming", "#ffb020", 6,
         [("scouting", "Scouting"), ("editing", "Cutting & Captions"), ("posting", "Posting")],
         "Finds clippable moments (GTA 6 etc.), cuts and captions them, posts via "
         "Upload-Post, tracks views/payouts; repurposes Steady's own content.",
         ["Pick creators whose terms allow clipping.", "Find moments.",
          "Cut + caption, credit the creator.", "Post (approval or Launch).",
          "Track views and payouts."],
         ["Only creators whose terms allow clipping; always credit the source."],
         ("clip", "clipping", "gta", "stream highlight", "vod", "highlight", "repurpose"),
         ["social", "video"],
         [("YOUTUBE_API_KEY", "find clippable moments on YouTube VODs"),
          ("TWITCH_CLIENT_ID", "find clippable moments on Twitch streams"),
          ("CLIP_PAYOUTS_SOURCE", "real views/payouts")],
         _ch(social=("tiktok", "youtube_shorts", "instagram")), budget_monthly_cents=5_000,
         proposed=[_spec("clip-scout", "clips: find clippable moments",
                         "Find clippable moments from permitted creators' VODs and streams",
                         "Only creators whose terms allow clipping. Credit the creator."),
                   _spec("clip-captioner", "clips: cut list and captions",
                         "Write cut lists and captions for approved moments",
                         "Short hook, creator credit, no clickbait lies.")]),
    Line("creators", "Creator Agency", "#ff6bd6", 7,
         [("calendar", "Content Calendar"), ("messages", "Captions & DMs"),
          ("earnings", "Earnings")],
         "Real creators who signed with Steady: calendar, SFW promo, caption/DM drafts that "
         "wait for approval, earnings tracking.",
         ["Plan each creator's week (SFW promo only).", "Draft captions and DMs.",
          "Everything waits for approval or a Launch scope.", "Track real earnings."],
         ["Agents never generate explicit content.",
          "Nothing is posted or sent without an approval or a Launch that covers it."],
         ("creator", "talent", "signed creator", "creator x", "dm"),
         ["social", "email"],
         [("CREATOR_EARNINGS_SOURCE", "real earnings per creator")],
         _ch(social=("instagram", "x"), note="one launch per creator"),
         budget_monthly_cents=3_000,
         proposed=[_spec("creator-planner", "creators: weekly SFW content calendar",
                         "Plan a signed creator's week of SFW promo posts",
                         "SFW only. Drafts wait for approval. Never write explicit content."),
                   _spec("caption-drafter", "creators: caption and DM drafts",
                         "Draft captions and DMs for a creator's approved calendar",
                         "SFW only; drafts only, approval before anything is sent.")]),
    Line("influencers", "AI Influencers", "#b388ff", 8,
         [("personas", "Personas & Brand Bibles"), ("studio", "Image, Video & Voice"),
          ("growth", "Scheduling & Growth")],
         "Original fictional personas (brand bible, visuals, voice), SFW content plan, "
         "fal.ai/ElevenLabs generation, AI-disclosure labels, scheduling, growth tracking.",
         ["Define an original fictional persona.", "Plan content; generate (cost first).",
          "Post with the AI label.", "Track growth."],
         ["Always labeled as AI; no impersonation; no fake engagement.", "SFW only."],
         ("ai influencer", "virtual model", "ai model", "persona", "ai account"),
         ["video", "social", "voice"],
         [("PLATFORM_ANALYTICS", "real follower growth")],
         _ch(social=("instagram", "tiktok"), note="one launch per persona"),
         budget_monthly_cents=5_000,
         proposed=[_spec("persona-planner", "influencers: AI persona planner",
                         "Plan a labeled AI persona's posts and generation prompts",
                         "SFW, always disclosed as AI, never resembles a real person.")]),
    Line("ecommerce", "E-commerce & Reselling", "#2ee6a8", 9,
         [("research", "Product Research"), ("store", "Store"), ("reselling", "FBA & eBay")],
         "Shopify stores, dropshipping/holiday funnels, Amazon FBA, eBay reselling "
         "research, product economics. Drafts until a Launch covers publishing.",
         ["Research products from sources.", "Model landed cost and margin.",
          "Draft listings and verify them.", "Publish only inside a Launch."],
         ["No fake reviews or invented claims.", "Drafts until a Launch covers publishing."],
         ("product", "shopify", "store", "listing", "e-com", "ecom", "dropship", "fba",
          "amazon fba", "ebay", "resell", "holiday"),
         ["shopify", "research"],
         [("AMAZON_SP_API_KEY", "Amazon FBA data (not built yet)"),
          ("EBAY_API_KEY", "eBay reselling research (not built yet)")],
         _ch(shopify=True), budget_monthly_cents=5_000,
         proposed=[_spec("product-researcher", "ecommerce: product researcher",
                         "Find product ideas within Steady's price and margin limits",
                         "Cite sources; compute landed cost and margin; respect the price "
                         "cap.")]),
    Line("community", "Community", "#5ce1e6", 10,
         [("roles", "Role Sync"), ("onboarding", "Onboarding"), ("renewals", "Renewals")],
         "Discord/Telegram role sync from Whop payments, onboarding DMs, renewals.",
         ["Sync roles from real Whop payments.", "Draft onboarding DMs (approved template).",
          "Renewal reminders inside the approved scope."],
         ["Roles only from real payment records.", "DMs only from approved templates."],
         ("discord", "telegram", "community", "members", "role sync", "renewal",
          "onboarding"),
         ["models", "whop", "discord", "telegram"],
         [],
         _ch(note="role sync and DMs run inside an approved scope"),
         budget_monthly_cents=1_000,
         proposed=[_spec("community-manager", "community: onboarding and renewals",
                         "Draft onboarding and renewal messages from approved templates",
                         "Only to paying members; templates approved by Steady.")]),
    Line("trading", "Trading (APEX-7 & bots)", "#ffd166", 11,
         [("research", "Research"), ("backtests", "Backtests & Sim"),
          ("propfirm", "Prop-Firm Rules"), ("reports", "Performance Reports")],
         "APEX-7, Steady's automated bots and indicators: research, backtests, paper/sim "
         "trading, prop-firm account rule checks, performance reports from real "
         "backtest/sim data only.",
         ["Research a strategy.", "Backtest on real historical data.",
          "Paper/sim trade.", "Check prop-firm rules.", "Report real results."],
         ["Live orders on real accounts are a restricted action: never inside a Launch, "
          "always Steady's explicit approval, with daily loss caps and a kill switch. Not "
          "built yet: Jackson refuses live orders until a broker connector with those "
          "controls exists.", "Results only from real backtest/sim data."],
         ("trading", "backtest", "apex", "apex-7", "mnq", "futures", "prop firm",
          "paper trade", "indicator", "trading strategy", "trading bot"),
         ["models"],
         [("APEX7_RESULTS_SOURCE", "real backtest/sim results from APEX-7"),
          ("MARKET_DATA_API_KEY", "historical data for backtests"),
          ("BROKER_API_KEY", "live orders (restricted; not built)")],
         _ch(note="Launches cover research and simulation only"),
         restricted=["live_trade"], budget_monthly_cents=2_000,
         proposed=[_spec("backtest-runner", "trading: backtests and sim reports",
                         "Run and report backtests/paper trading from real data",
                         "Real data only. Simulation only. Never place orders.")]),
    Line("realestate", "Real Estate (wholesaling)", "#c9a46a", 12,
         [("comps", "Comps Research"), ("underwriting", "Underwriting"),
          ("leads", "Lead Lists")],
         "Wholesaling: comps research, underwriting, lead lists. Research and simulation "
         "only until Steady approves.",
         ["Comps from permitted sources.", "Underwrite (ARV, repairs, MAO).",
          "Build lawful lead lists.", "Offers only after Steady approves."],
         ["Research/simulation only until Steady approves; no offers or contracts sent "
          "automatically."],
         ("real estate", "wholesal", "comps", "arv", "property", "underwrit", "mao"),
         ["models"], [("PROPERTY_DATA_API_KEY", "comps and property data (not built)")],
         _ch(note="research and simulation only"), budget_monthly_cents=1_000,
         proposed=[_spec("underwriter", "real estate: comps and underwriting",
                         "Underwrite a property from comps Steady provides",
                         "Show the math; cite comps; simulation only.")]),
    Line("legal", "Legal & Admin", "#9aa5b1", 13,
         [("drafts", "Drafts"), ("review", "Steady's Review")],
         "Contracts, filings and packets drafted from templates; always routed to Steady "
         "for review, never filed automatically.",
         ["Draft from an approved template.",
          "Route to Steady (and a qualified reviewer).",
          "Nothing is filed or signed by an agent."],
         ["Never filed or sent automatically; drafts only, not legal advice."],
         ("contract", "agreement", "nda", "filing", "llc", "legal", "terms of service",
          "packet"),
         ["models"], [], _ch(note="legal documents are never inside a Launch"),
         restricted=["filing"], budget_monthly_cents=500,
         proposed=[_spec("contract-drafter", "legal: drafts from templates",
                         "Draft contracts and packets from Steady's templates for review",
                         "Drafts only; flag anything that needs a lawyer; never file.")]),
    Line("finance", "Finance & Ops", "#ffe08a", 14,
         [("briefing", "CEO Briefing"), ("ledger", "Ledger & Forecast"),
          ("ops", "Ops & Agents")],
         "Cost ledger, revenue by business (real sources only), cash forecast, budgets, "
         "daily CEO briefing across all departments.",
         ["Track spend per department and agent.", "Record real revenue by business.",
          "Enforce budgets (cost guard).", "Daily briefing across all departments."],
         ["Revenue is actual only when a connected source or a referenced entry reports "
          "it.", "Agents can't create agents; Jackson proposes, Steady approves."],
         ("spend", "revenue", "budget", "briefing", "finance", "cost", "profit", "report",
          "summary", "recap", "forecast", "cash"),
         ["models"], [], {}, budget_monthly_cents=0),
]
BY_KEY = {ln.key: ln for ln in LINES}
LINE_KEYS = tuple(ln.key for ln in LINES)


def line_of_text(text: str) -> tuple[Line | None, list[str]]:
    """Which department a command is about, and the words that showed it."""
    low = text.lower()
    best, hits = None, []
    for ln in LINES:  # a keyword matches at the start of a word ("wholesal" -> "wholesaling")
        h = [w for w in ln.keywords if re.search(rf"(?<![a-z0-9]){re.escape(w)}", low)]
        # more matching words wins; on a tie a specific department beats the cross-cutting
        better = len(h) > len(hits) or (h and len(h) == len(hits) and best is not None
                                        and best.key in ("rnd", "finance", "marketing"))
        if better:
            best, hits = ln, h
    return best, hits


# --- KPIs: real rows only -----------------------------------------------------------------

def _kpi(name, value, unit="", source="", note=None):
    return {"name": name, "value": value, "unit": unit, "source": source,
            "note": note if value is None else None}


def kpis(db: Session, tenant_id, key: str) -> list[dict]:  # noqa: C901 - one branch per dept
    from .models import (
        Agent,
        Artifact,
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
        SiteProject,
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
                                    RevenueEntry.period == month.strftime("%Y-%m"))) or 0) / 100

    def members(scope=biz, **where):
        from .connectors.whop import ACCESS
        from .models import WhopMember

        if not count(select(func.count()).select_from(WhopMember).where(
                WhopMember.tenant_id == tenant_id)):
            return None  # Whop hasn't reported anyone yet
        q = select(func.count()).select_from(WhopMember).where(
            WhopMember.tenant_id == tenant_id, WhopMember.status.in_(ACCESS))
        if scope is not None:
            q = q.where(WhopMember.business_id.in_(scope))
        for col, want in where.items():
            c = getattr(WhopMember, col)
            q = q.where(c == want if isinstance(want, str) else
                        c.is_not(None) if want else c.is_(None))
        return count(q)

    def posts(statuses=("published",), channels=None, since=month, scope=biz):
        q = select(func.count()).select_from(ContentItem).where(
            ContentItem.tenant_id == tenant_id, ContentItem.status.in_(statuses),
            ContentItem.updated_at >= since)
        if scope is not None:
            q = q.where(ContentItem.business_id.in_(scope))
        if channels:
            q = q.where(ContentItem.channel.in_(channels))
        return count(q)

    def emails(scope=biz):
        q = select(func.count()).select_from(OutreachMessage).where(
            OutreachMessage.tenant_id == tenant_id, OutreachMessage.status == "sent",
            OutreachMessage.sent_at >= month)
        if scope is not None:
            q = q.where(OutreachMessage.campaign_id.in_(select(OutreachCampaign.id).where(
                OutreachCampaign.business_id.in_(scope))))
        return count(q)

    def stage(s, scope=biz):
        q = select(func.count()).select_from(Contact).where(Contact.tenant_id == tenant_id,
                                                           Contact.stage == s)
        if scope is not None:
            q = q.where(Contact.business_id.in_(scope))
        return count(q)

    if key == "rnd":
        n = count(select(func.count()).select_from(Opportunity).where(
            Opportunity.tenant_id == tenant_id))
        return [
            _kpi("New opportunities (7 days)", count(select(func.count()).select_from(
                Opportunity).where(Opportunity.tenant_id == tenant_id,
                                   Opportunity.created_at >= week)), "", "opportunities"),
            _kpi("Average score", round(float(db.scalar(select(func.avg(Opportunity.score))
                 .where(Opportunity.tenant_id == tenant_id)) or 0), 1) if n else None,
                 "/100", "opportunities", "no opportunities scored yet"),
        ]
    if key == "courses":
        return [
            _kpi("Education businesses", count(select(func.count()).select_from(Business)
                 .where(Business.tenant_id == tenant_id, Business.line == "courses")), "",
                 "businesses"),
            _kpi("Course revenue this month (Whop)", revenue("whop"), "USD",
                 "revenue_entries", "Whop isn't connected yet: no revenue until it reports"),
            _kpi("Students with access (Whop)", members(), "", "whop_members",
                 "no Whop members reported yet (connect WHOP_API_KEY, then Sync)"),
        ]
    if key == "marketing":
        brands = count(select(func.count(func.distinct(ContentItem.business_id))).where(
            ContentItem.tenant_id == tenant_id, ContentItem.status == "published",
            ContentItem.updated_at >= month))
        return [_kpi("Posts published this month (all brands)", posts(scope=None), "",
                     "content_items"),
                _kpi("Brands with posts this month", brands, "", "content_items"),
                _kpi("Emails sent this month (all brands)", emails(scope=None), "",
                     "outreach_messages")]
    if key == "agency":
        return [_kpi("Active clients", stage("won"), "", "contacts (stage won)"),
                _kpi("Open proposals", stage("proposal"), "", "contacts (stage proposal)"),
                _kpi("Deliverables published this month", posts(), "", "content_items")]
    if key == "sales":
        new = count(select(func.count()).select_from(Contact).where(
            Contact.tenant_id == tenant_id, Contact.created_at >= week))
        return [_kpi("New leads (7 days)", new, "", "contacts"),
                _kpi("Meetings booked", stage("meeting", None), "",
                     "contacts (stage meeting)"),
                _kpi("Won", stage("won", None), "", "contacts (stage won)"),
                _kpi("Outreach emails sent this month", emails(scope=None), "",
                     "outreach_messages")]
    if key == "factory":
        sites = count(select(func.count()).select_from(SiteProject).where(
            SiteProject.tenant_id == tenant_id))
        live = count(select(func.count()).select_from(SiteProject).where(
            SiteProject.tenant_id == tenant_id, SiteProject.status == "live"))
        hosted = count(select(func.count()).select_from(SiteProject).where(
            SiteProject.tenant_id == tenant_id, SiteProject.status == "live",
            SiteProject.live_url.like("https://%")))
        return [_kpi("Sites & apps", sites, "", "sites"), _kpi("Live", live, "", "sites"),
                _kpi("Published on real hosting", hosted, "", "sites (https live URL)")]
    if key == "clips":
        return [_kpi("Clips posted this month", posts(channels=("tiktok", "youtube_shorts",
                                                                 "instagram")), "",
                     "content_items"),
                _kpi("Views", None, "", "", "needs a views source (platform analytics)"),
                _kpi("Payouts", None, "USD", "", "needs CLIP_PAYOUTS_SOURCE")]
    if key == "creators":
        return [_kpi("Drafts waiting for approval", posts(("in_review",),
                     since=month - timedelta(days=365)), "", "content_items"),
                _kpi("Posts published this month", posts(), "", "content_items"),
                _kpi("Creator earnings", None, "USD", "", "needs CREATOR_EARNINGS_SOURCE")]
    if key == "influencers":
        return [_kpi("AI personas", count(select(func.count()).select_from(Persona).where(
                    Persona.tenant_id == tenant_id, Persona.kind == "virtual_influencer")),
                     "", "personas"),
                _kpi("Posts published this month", posts(), "", "content_items"),
                _kpi("Followers gained", None, "", "", "needs PLATFORM_ANALYTICS")]
    if key == "ecommerce":
        return [_kpi("Draft products", count(select(func.count()).select_from(ShopProduct)
                     .where(ShopProduct.tenant_id == tenant_id,
                            ShopProduct.status == "DRAFT")), "", "shop_products"),
                _kpi("Published products", count(select(func.count()).select_from(
                    ShopProduct).where(ShopProduct.tenant_id == tenant_id,
                                       ShopProduct.status == "ACTIVE")), "", "shop_products"),
                _kpi("Revenue this month (Shopify)", revenue("shopify"), "USD",
                     "revenue_entries", "no Shopify sales reported yet")]
    if key == "community":
        return [_kpi("Paying members (Whop, all businesses)", members(scope=None), "",
                     "whop_members", "no Whop members reported yet"),
                _kpi("Members with Discord linked", members(scope=None, discord_id=True), "",
                     "whop_members", "no Whop members reported yet"),
                _kpi("Discord roles granted", members(scope=None, discord_synced="granted"), "",
                     "whop_members", "no Whop members reported yet"),
                _kpi("Renewals this month", None, "", "",
                     "counted once Whop has reported a full month of payments")]
    if key == "trading":
        return [_kpi("Backtests run", None, "", "",
                     "needs APEX7_RESULTS_SOURCE (real backtest/sim data)"),
                _kpi("Sim P&L", None, "USD", "", "needs APEX7_RESULTS_SOURCE"),
                _kpi("Live orders placed", 0, "", "policy (refused until a broker connector "
                     "with loss caps and a kill switch exists)")]
    if key == "realestate":
        return [_kpi("Deals analyzed", count(select(func.count()).select_from(Opportunity)
                     .where(Opportunity.tenant_id == tenant_id,
                            Opportunity.category == "real_estate")), "", "opportunities"),
                _kpi("Comps data", None, "", "", "needs PROPERTY_DATA_API_KEY")]
    if key == "legal":
        drafts = count(select(func.count()).select_from(Artifact).where(
            Artifact.tenant_id == tenant_id, Artifact.kind == "legal_draft"))
        return [_kpi("Drafts waiting for your review", drafts, "", "artifacts")]
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
        alerts = count(select(func.count()).select_from(Event).where(
            Event.tenant_id == tenant_id, Event.type == "alert.raised", Event.ts >= week))
        agents = count(select(func.count()).select_from(Agent).where(
            Agent.tenant_id == tenant_id))
        return [_kpi("Spend this month", round(model + ledger, 2), "USD",
                     "model_usage + ledger"),
                _kpi("Actual revenue this month (all businesses)", actual, "USD",
                     "revenue_entries"),
                _kpi("Cash forecast", None, "USD", "",
                     "needs real revenue history (Whop/Shopify connected)"),
                _kpi("Alerts (7 days)", alerts, "", "events"),
                _kpi("Agents on staff", agents, "", "agents")]
    return []
