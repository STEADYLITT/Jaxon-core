"""The City (phase 23, ADR-037): the agent registry and `emit()`.

Rule zero: nothing fake. Every row here comes from a real agent action:
- the registry lists only agents that exist in code (task handlers in the worker) or as an
  AgentSpec the owner created; departments without one are shown as "not staffed";
- `emit()` is called by the worker and the API at the moment something really happened,
  in the same transaction as the state change, so the event and the truth commit together;
- statuses come from real task transitions or the owner's pause/kill; a worker that stops
  heartbeating turns its agents gray (`sweep_offline`).
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Agent, AgentSpec, Department, Event, Floor, Task

HEARTBEAT_S = 15
OFFLINE_AFTER_S = 60

# Departments are Steady's business lines (lines.py, ADR-039):
# (key, name, color, slot, [(floor key, floor name)])
from .lines import LINES  # noqa: E402

DEPARTMENTS = [(ln.key, ln.name, ln.color, ln.slot, ln.floors) for ln in LINES]

# The real workers: task kind -> (line or "jaxon", floor, name, role)
BUILT_IN = {
    "plan": ("jaxon", None, "Jaxon Planner", "turns Steady's commands into plans"),
    "launch_propose": ("jaxon", None, "Launch Planner", "proposes launches for approval"),
    "course_build": ("courses", "design", "Course Builder",
                     "builds course drafts: outline, lessons, student platform, sales page"),
    "course_publish": ("courses", "launch", "Course Publisher",
                       "publishes approved courses (student platform + sales page)"),
    "voice_generate": ("courses", "lessons", "Voice Narrator",
                       "narrates lessons and ad scripts with AI voice (cost approved first)"),
    "whop_sync": ("courses", "launch", "Sales Recorder",
                  "records real Whop members and sales (never estimated)"),
    "web_research": ("rnd", "board", "Web Researcher",
                     "Google search, Trends and Maps research via Apify (cost cap approved)"),
    "community_sync": ("community", "roles", "Role Syncer",
                       "gives and removes Discord/Telegram access from real Whop memberships"),
    "community_announce": ("community", "onboarding", "Announcer",
                           "posts approved announcements to Discord/Telegram"),
    "property_comps": ("realestate", "comps", "Comps Researcher",
                       "pulls comparable sales from BatchData (research only)"),
    "skip_trace": ("realestate", "leads", "Skip Tracer",
                   "finds a property owner's contact data, only with your approval"),
    "apex7_import": ("trading", "backtests", "Results Recorder",
                     "records APEX-7's real backtest/sim trades and their stats"),
    "futures_bars": ("trading", "research", "Market Data Desk",
                     "pulls real CME futures bars from Databento (cost approved)"),
    "paper_snapshot": ("trading", "reports", "Paper Account Reporter",
                       "reads APEX-7's Alpaca PAPER account and market data"),
    "paper_order": ("trading", "backtests", "Paper Trader",
                    "places PAPER orders only (live orders are refused)"),
    "ad_draft": ("marketing", "ads", "Ads Planner",
                 "drafts paused Meta/TikTok campaigns (spend nothing)"),
    "ad_activate": ("marketing", "ads", "Ads Launcher",
                    "turns ads on only inside an approved Launch"),
    "ad_pause": ("marketing", "ads", "Ads Stopper", "pauses ads (kill switch)"),
    "ad_creative": ("marketing", "ads", "Ad Builder",
                    "adds the creative and the ad to a paused campaign"),
    "ad_insights": ("marketing", "ads", "Ads Reporter",
                    "reads real spend and results back from Meta/TikTok"),
    "clip_scout": ("clips", "scouting", "Clip Scout",
                   "finds clippable YouTube/Twitch moments from permitted creators"),
    "opportunity_research": ("rnd", "board", "Research Scout",
                             "researches opportunities from cited sources"),
    "content_generate": ("marketing", "content", "Content Writer", "drafts posts in a voice"),
    "content_publish": ("marketing", "content", "Social Publisher", "posts approved content"),
    "outreach_prepare": ("marketing", "email", "Outreach Writer", "prepares and reviews campaigns"),
    "outreach_send": ("marketing", "email", "Outreach Sender", "sends approved email"),
    "site_build": ("factory", "build", "Site Builder", "builds websites"),
    "site_launch": ("factory", "deploy", "Site Launcher", "launches websites"),
    "repo_push": ("factory", "build", "Repo Keeper",
                  "puts approved builds in private GitHub repos"),
    "content_video": ("influencers", "studio", "Video Producer", "makes short clips (fal.ai)"),
    "marketplace_research": ("ecommerce", "reselling", "Marketplace Researcher",
                             "Amazon/eBay prices, ranks, FBA fees and margins (cited)"),
    "store_build": ("ecommerce", "store", "Store Builder", "builds Shopify drafts"),
    "shopify_publish": ("ecommerce", "store", "Shop Publisher", "publishes products"),
    "agent_factory": ("finance", "ops", "Agent Factory", "drafts new agents"),
    "agent_eval": ("finance", "ops", "Agent Evaluator", "tests agents in a sandbox"),
    "daily_report": ("finance", "briefing", "Daily Reporter", "writes Steady's CEO briefing"),
}

SPEC_DEPARTMENT = [  # keyword in an AgentSpec's name/role -> department (no business)
    (("clip",), "clips"),
    (("creator", "caption-drafter"), "creators"),
    (("persona", "influencer", "virtual"), "influencers"),
    (("backtest", "trading", "apex"), "trading"),
    (("underwrit", "real estate", "wholesal"), "realestate"),
    (("contract", "legal"), "legal"),
    (("community", "discord", "telegram"), "community"),
    (("lead", "call", "sales"), "sales"),
    (("web", "qa", "app", "saas", "site"), "factory"),
    (("product", "shopify", "store", "ecommerce"), "ecommerce"),
    (("client", "agency"), "agency"),
    (("course", "lesson", "student"), "courses"),
    (("funnel", "marketing"), "marketing"),
    (("finance", "budget", "invoice", "briefing"), "finance"),
]


def now() -> datetime:
    return datetime.now(UTC)


def demo_mode() -> bool:
    return os.environ.get("JAXON_DEMO", "").lower() == "true"


# --- emit() ---------------------------------------------------------------------------------

def emit(db: Session, tenant_id, type_: str, *, from_: tuple[str, str] | None = None,
         to: tuple[str, str] | None = None, run_id=None, task_id=None, agent_id=None,
         payload: dict | None = None) -> Event:
    """Record one real event and announce it (pg_notify fires when the caller commits)."""
    ev = Event(tenant_id=tenant_id, type=type_, run_id=run_id, task_id=task_id,
               agent_id=agent_id, payload=payload or {},
               from_kind=from_[0] if from_ else None, from_id=str(from_[1]) if from_ else None,
               to_kind=to[0] if to else None, to_id=str(to[1]) if to else None)
    db.add(ev)
    db.flush()
    db.execute(text("SELECT pg_notify('jackson_events', :p)"),
               {"p": f"{tenant_id}:{ev.id}"})
    return ev


# --- Registry -------------------------------------------------------------------------------

def _upsert(db: Session, model, values: dict, conflict: list[str]):
    db.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=conflict))


def ensure_departments(db: Session, tenant_id) -> dict[str, Department]:
    have = {d.key: d for d in db.scalars(select(Department).where(
        Department.tenant_id == tenant_id))}
    if not {d[0] for d in DEPARTMENTS} <= set(have):
        for key, name, color, slot, _floors in DEPARTMENTS:
            _upsert(db, Department, {"id": uuid.uuid4(), "tenant_id": tenant_id, "key": key,
                                     "name": name, "color": color, "slot": slot},
                    ["tenant_id", "key"])
        db.flush()
        have = {d.key: d for d in db.scalars(select(Department).where(
            Department.tenant_id == tenant_id))}
        for key, _n, _c, _s, floors in DEPARTMENTS:
            for level, (fk, fname) in enumerate(floors, start=1):
                _upsert(db, Floor, {"id": uuid.uuid4(), "tenant_id": tenant_id,
                                    "department_id": have[key].id, "key": fk, "name": fname,
                                    "level": level}, ["tenant_id", "department_id", "key"])
        db.flush()
    return have


def _floor(db: Session, tenant_id, dept: Department | None, key: str | None):
    if dept is None or key is None:
        return None
    return db.scalar(select(Floor.id).where(Floor.tenant_id == tenant_id,
                                            Floor.department_id == dept.id, Floor.key == key))


def spec_department(name: str, role: str) -> str:
    hay = f"{name} {role}".lower()
    for words, dept in SPEC_DEPARTMENT:
        if any(w in hay for w in words):
            return dept
    return "rnd"


def ensure_agent(db: Session, tenant_id, kind: str, spec_name: str | None = None,
                 spec_role: str | None = None, spec_line: str | None = None) -> Agent:
    """The registry row for a real worker, created on first sight and re-placed when the
    line it belongs to changes (its status and history are untouched)."""
    key = f"spec:{spec_name}" if spec_name else f"kind:{kind}"
    if spec_name:
        dkey, fkey = spec_line or spec_department(spec_name, spec_role or ""), None
        name, role = spec_name, spec_role or "custom agent"
    else:
        dkey, fkey, name, role = BUILT_IN.get(kind, ("rnd", None, kind, kind))
    a = db.scalar(select(Agent).where(Agent.tenant_id == tenant_id, Agent.key == key))
    depts = ensure_departments(db, tenant_id)
    dept = depts.get(dkey)  # None for Jaxon's own agents (they live in the orb)
    floor = _floor(db, tenant_id, dept, fkey)
    if dept is not None and floor is None:  # AgentSpec agents get the line's first floor
        floor = db.scalar(select(Floor.id).where(Floor.department_id == dept.id)
                          .order_by(Floor.level).limit(1))
    dept_id = dept.id if dept else None
    if a is not None:
        if (a.department_id, a.floor_id) != (dept_id, floor):
            a.department_id, a.floor_id = dept_id, floor
            db.flush()
        return a
    _upsert(db, Agent, {"id": uuid.uuid4(), "tenant_id": tenant_id, "key": key, "name": name,
                        "role": role[:200], "task_kind": kind, "department_id": dept_id,
                        "floor_id": floor, "status": "idle"}, ["tenant_id", "key"])
    db.flush()
    return db.scalar(select(Agent).where(Agent.tenant_id == tenant_id, Agent.key == key))


def _spec_line(db: Session, spec: AgentSpec) -> str | None:
    from .models import Business

    if spec.business_id is None:
        return None
    return db.scalar(select(Business.line).where(Business.id == spec.business_id))


def ensure_registry(db: Session, tenant_id) -> None:
    """Register every built-in worker and every active AgentSpec of the tenant."""
    for kind in BUILT_IN:
        ensure_agent(db, tenant_id, kind)
    for s in db.scalars(select(AgentSpec).where(AgentSpec.tenant_id == tenant_id,
                                                AgentSpec.status == "active")):
        ensure_agent(db, tenant_id, "agent", s.name, s.role, _spec_line(db, s))


def agent_for_task(db: Session, task: Task) -> Agent:
    if task.agent_spec_id is not None:
        s = db.get(AgentSpec, task.agent_spec_id)
        if s is not None:
            return ensure_agent(db, task.tenant_id, "agent", s.name, s.role, _spec_line(db, s))
    return ensure_agent(db, task.tenant_id, task.kind)


def address(agent: Agent) -> tuple[str, str]:
    """Where an envelope for this agent goes: a department building, or the Jaxon orb."""
    return ("department", str(agent.department_id)) if agent.department_id else ("jaxon", "jaxon")


def set_status(db: Session, agent: Agent, status: str, task_id=None) -> None:
    if agent.status in ("paused", "killed") and status not in ("paused", "killed", "idle"):
        status = agent.status  # the owner's hold wins; only the owner lifts it
    if agent.status in ("paused", "killed"):
        agent.current_task_id = task_id
        return
    agent.status = status
    agent.current_task_id = task_id
    agent.status_changed_at = now()


def other_running(db: Session, agent: Agent, task_id) -> uuid.UUID | None:
    """Another task of the same agent still running (agents can work in parallel)."""
    from .contracts import TaskState

    q = select(Task.id).where(Task.tenant_id == agent.tenant_id, Task.id != task_id,
                              Task.status == TaskState.RUNNING.value)
    if agent.key.startswith("spec:"):
        q = q.where(Task.agent_spec_id.in_(select(AgentSpec.id).where(
            AgentSpec.tenant_id == agent.tenant_id, AgentSpec.name == agent.key[5:])))
    else:
        q = q.where(Task.kind == agent.task_kind, Task.agent_spec_id.is_(None))
    return db.scalar(q.limit(1))


def on_task(db: Session, task: Task, type_: str, status: str | None = None,
            **payload) -> Agent:
    """Worker/API hook for a real task transition: update the agent, emit the event.
    Envelopes: task.created goes Jaxon -> the agent's building; task.completed/failed is
    reported back building -> Jaxon; approval.requested goes building -> Steady (CEO)."""
    a = agent_for_task(db, task)
    if status is not None:
        if status in ("done", "failed", "idle") and other_running(db, a, task.id):
            status, keep = "working", other_running(db, a, task.id)
        else:
            keep = task.id if status in ("working", "waiting") else None
        set_status(db, a, status, keep)
    here = address(a)
    route = {"task.created": (("jaxon", "jaxon"), here),
             "task.completed": (here, ("jaxon", "jaxon")),
             "task.failed": (here, ("jaxon", "jaxon")),
             "approval.requested": (here, ("ceo", "steady"))}.get(type_, (None, None))
    emit(db, task.tenant_id, type_, from_=route[0], to=route[1], run_id=task.run_id,
         task_id=task.id, agent_id=a.id, payload={"title": task.title[:200], "kind": task.kind,
                                                  **payload})
    return a


# --- Heartbeats and offline -----------------------------------------------------------------

def heartbeat(db: Session, running: list[Task]) -> int:
    """Called every HEARTBEAT_S by a live worker: every registered agent is reachable; the
    running ones get an agent.heartbeat event naming their task."""
    t = now()
    db.execute(update(Agent).values(last_heartbeat_at=t))
    back = db.scalars(select(Agent).where(Agent.status == "offline")).all()
    for a in back:  # the worker came back: show the truth again
        a.status, a.status_changed_at = "idle", t
        emit(db, a.tenant_id, "agent.heartbeat", agent_id=a.id, payload={"back_online": True})
    for task in running:
        a = agent_for_task(db, task)
        emit(db, task.tenant_id, "agent.heartbeat", agent_id=a.id, task_id=task.id,
             run_id=task.run_id, payload={"task": task.title[:200]})
    return len(running)


def sweep_offline(db: Session, at: datetime | None = None) -> list[uuid.UUID]:
    """No heartbeat for OFFLINE_AFTER_S -> the agent is offline (gray). Runs in the API, so it
    works precisely when the worker is gone."""
    cutoff = (at or now()) - timedelta(seconds=OFFLINE_AFTER_S)
    stale = db.scalars(select(Agent).where(
        Agent.status.not_in(("offline", "paused", "killed")),
        (Agent.last_heartbeat_at.is_(None)) | (Agent.last_heartbeat_at < cutoff))).all()
    out = []
    for a in stale:  # never heartbeated = no worker has ever served it: offline too
        a.status, a.status_changed_at = "offline", at or now()
        emit(db, a.tenant_id, "agent.offline", agent_id=a.id, payload={
            "last_heartbeat_at": a.last_heartbeat_at.isoformat() if a.last_heartbeat_at
            else None})
        out.append(a.id)
    return out


# --- Phase 6: cost guard and failure siren ---------------------------------------------------

SIREN_STREAK = 3


def default_daily_limit_usd() -> float:
    try:
        return float(os.environ.get("CITY_AGENT_DAILY_LIMIT_USD", "5"))
    except ValueError:
        return 5.0


def daily_limit(agent: Agent) -> float:
    return float(agent.daily_limit_usd) if agent.daily_limit_usd is not None \
        else default_daily_limit_usd()


def cost_today(db: Session, agent: Agent) -> float:
    day = now().replace(hour=0, minute=0, second=0, microsecond=0)
    return float(sum(float(e.payload.get("usd") or 0) for e in db.scalars(select(Event).where(
        Event.agent_id == agent.id, Event.type == "cost.recorded", Event.ts >= day))))


def alert(db: Session, agent: Agent, kind: str, message: str, **payload) -> Event:
    """A red letter from the agent's building to Steady's inbox."""
    return emit(db, agent.tenant_id, "alert.raised", from_=address(agent), to=("ceo", "steady"),
                agent_id=agent.id, payload={"kind": kind, "message": message,
                                            "agent": agent.name, **payload})


def cost_guard(db: Session, agent: Agent) -> bool:
    """Over its daily limit: pause the agent (and its current run) first, then tell Steady."""
    from .models import WorkflowRun

    if agent.status in ("paused", "killed"):
        return False
    spent, limit = cost_today(db, agent), daily_limit(agent)
    if spent <= limit:
        return False
    agent.status, agent.status_changed_at = "paused", now()
    task = db.get(Task, agent.current_task_id) if agent.current_task_id else None
    if task is not None:
        run = db.get(WorkflowRun, task.run_id)
        if run is not None and not run.paused and run.status not in ("succeeded", "failed",
                                                                      "canceled"):
            run.paused = True
    db.flush()  # the hold is in place before anyone hears about it
    emit(db, agent.tenant_id, "agent.paused", agent_id=agent.id,
         payload={"reason": "cost guard", "spent_usd": round(spent, 4), "limit_usd": limit})
    alert(db, agent, "cost_guard",
          f"{agent.name} spent ${spent:.2f} today, over its ${limit:.2f} limit. It is paused "
          "until you resume it.", spent_usd=round(spent, 4), limit_usd=limit)
    return True


def failure_siren(db: Session, agent: Agent) -> bool:
    """Three failures in a row (and only once per streak): the building flashes red."""
    last = [e.type for e in db.scalars(select(Event).where(
        Event.agent_id == agent.id, Event.type.in_(("task.completed", "task.failed")))
        .order_by(Event.id.desc()).limit(SIREN_STREAK + 1))]
    streak = last[:SIREN_STREAK]
    if len(streak) < SIREN_STREAK or set(streak) != {"task.failed"}:
        return False
    if len(last) > SIREN_STREAK and last[SIREN_STREAK] == "task.failed":
        return False  # already sounded for this streak
    alert(db, agent, "failure_siren",
          f"{agent.name} failed {SIREN_STREAK} times in a row. Check its log before it "
          "runs again.", department_id=str(agent.department_id) if agent.department_id else None)
    return True
