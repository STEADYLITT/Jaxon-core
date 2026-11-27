"""The City API (phase 23, ADR-037): one snapshot the page always rebuilds from, a live
event stream, and an events query. Read-only here; agent controls are in phase 4.

Paths follow this API's tenant-scoped style:
  GET /tenants/{t}/city/state    (spec: /api/city/state)
  GET /tenants/{t}/city/stream   (spec: /api/city/stream, SSE)
  GET /tenants/{t}/city/events   (spec: /api/events?since=&agent=&task=)
Commands and approvals reuse POST /tenants/{t}/commands and
POST /tenants/{t}/approvals/{id}/decision.
"""

import json
import re
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import city
from .contracts import TaskState
from .db import bind_tenant, make_sessionmaker
from .deps import DB, TenantMember, TenantWriter
from .models import (
    Agent,
    Approval,
    Department,
    Event,
    Floor,
    LedgerEntry,
    ModelUsage,
    RevenueEntry,
    Task,
    WorkflowRun,
)
from .settings import load_settings

router = APIRouter()


def _iso(d):
    return d.isoformat() if d else None


def event_dict(e: Event) -> dict:
    return {"id": e.id, "ts": _iso(e.ts), "type": e.type,
            "from": {"kind": e.from_kind, "id": e.from_id} if e.from_kind else None,
            "to": {"kind": e.to_kind, "id": e.to_id} if e.to_kind else None,
            "run_id": str(e.run_id) if e.run_id else None,
            "task_id": str(e.task_id) if e.task_id else None,
            "agent_id": str(e.agent_id) if e.agent_id else None, "payload": e.payload}


def _day_start() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def snapshot(db: Session, tenant_id: uuid.UUID) -> dict:
    city.ensure_registry(db, tenant_id)
    city.sweep_offline(db)
    db.flush()
    now, today = datetime.now(UTC), _day_start()
    line_keys = [d[0] for d in city.DEPARTMENTS]
    depts = db.scalars(select(Department).where(Department.tenant_id == tenant_id,
                                                Department.key.in_(line_keys))
                       .order_by(Department.slot)).all()
    floors = db.scalars(select(Floor).where(Floor.tenant_id == tenant_id)
                        .order_by(Floor.level)).all()
    agents = db.scalars(select(Agent).where(Agent.tenant_id == tenant_id)
                        .order_by(Agent.name)).all()

    # Per-agent numbers, all from events the agents wrote.
    counts: dict = {}
    for aid, typ, n in db.execute(
            select(Event.agent_id, Event.type, func.count()).where(
                Event.tenant_id == tenant_id, Event.ts >= today,
                Event.type.in_(("task.completed", "task.failed")))
            .group_by(Event.agent_id, Event.type)):
        counts.setdefault(aid, {})[typ] = n
    week: dict = {}
    for aid, typ, n in db.execute(
            select(Event.agent_id, Event.type, func.count()).where(
                Event.tenant_id == tenant_id, Event.ts >= now - timedelta(days=7),
                Event.type.in_(("task.completed", "task.failed")))
            .group_by(Event.agent_id, Event.type)):
        week.setdefault(aid, {})[typ] = n
    cost: dict = {}
    for e in db.scalars(select(Event).where(Event.tenant_id == tenant_id, Event.ts >= today,
                                            Event.type == "cost.recorded")):
        cost[e.agent_id] = cost.get(e.agent_id, 0.0) + float(e.payload.get("usd") or 0)
    current = {t.id: t for t in db.scalars(select(Task).where(
        Task.id.in_([a.current_task_id for a in agents if a.current_task_id])))}
    commands = {r.id: r for r in db.scalars(select(WorkflowRun).where(
        WorkflowRun.id.in_([t.run_id for t in current.values()])))}

    def agent_dict(a: Agent) -> dict:
        t = current.get(a.current_task_id)
        w = week.get(a.id, {})
        tried = w.get("task.completed", 0) + w.get("task.failed", 0)
        fresh = a.last_heartbeat_at is not None and \
            now - a.last_heartbeat_at <= timedelta(seconds=city.OFFLINE_AFTER_S)
        return {"id": str(a.id), "name": a.name, "role": a.role, "key": a.key,
                "department_id": str(a.department_id) if a.department_id else None,
                "floor_id": str(a.floor_id) if a.floor_id else None,
                "status": a.status, "online": fresh and a.status not in ("offline", "killed"),
                "model": a.model, "last_heartbeat_at": _iso(a.last_heartbeat_at),
                "status_changed_at": _iso(a.status_changed_at),
                "current_task": {"id": str(t.id), "title": t.title, "status": t.status,
                                 "started_at": _iso(t.started_at), "run_id": str(t.run_id),
                                 "command": commands[t.run_id].objective
                                 if t.run_id in commands else None} if t else None,
                "cost_today_usd": round(cost.get(a.id, 0.0), 6),
                "daily_limit_usd": city.daily_limit(a),
                "done_today": counts.get(a.id, {}).get("task.completed", 0),
                "failed_today": counts.get(a.id, {}).get("task.failed", 0),
                "success_rate_7d": round(w.get("task.completed", 0) / tried, 3)
                if tried else None}

    staffed = {a.department_id for a in agents if a.department_id}
    floor_agents: dict = {}
    for a in agents:
        if a.floor_id:
            floor_agents.setdefault(a.floor_id, []).append(str(a.id))

    def task_count(*states, since=None):
        q = select(func.count()).select_from(Task).where(Task.tenant_id == tenant_id,
                                                         Task.status.in_(states))
        if since is not None:
            q = q.where(Task.finished_at >= since)
        return db.scalar(q) or 0

    model_micros = db.scalar(select(func.coalesce(func.sum(ModelUsage.cost_usd_micros), 0))
                             .where(ModelUsage.tenant_id == tenant_id,
                                    ModelUsage.created_at >= today)) or 0
    ledger_cents = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
                             .where(LedgerEntry.tenant_id == tenant_id,
                                    LedgerEntry.kind == "commit",
                                    LedgerEntry.created_at >= today)) or 0
    connected = db.scalar(select(func.count()).select_from(RevenueEntry).where(
        RevenueEntry.tenant_id == tenant_id, RevenueEntry.source != "manual")) or 0
    revenue = None
    if connected:  # only a connected source (Shopify/Whop/Stripe/import), never estimated
        revenue = int(db.scalar(select(func.coalesce(func.sum(RevenueEntry.amount_cents), 0))
                             .where(RevenueEntry.tenant_id == tenant_id,
                                    RevenueEntry.kind == "actual",
                                    RevenueEntry.source != "manual",
                                    RevenueEntry.created_at >= today)) or 0) / 100

    pending = db.scalars(select(Approval).where(Approval.tenant_id == tenant_id,
                                                Approval.status == "pending")
                         .order_by(Approval.created_at)).all()
    inbox = []
    for ap in pending:
        t = db.get(Task, ap.task_id)
        a = city.agent_for_task(db, t) if t else None
        inbox.append({"id": str(ap.id), "question": ap.description, "category": ap.category,
                      "amount_cents": ap.amount_cents, "reason": ap.reason,
                      "task_id": str(ap.task_id), "run_id": str(ap.run_id),
                      "agent_id": str(a.id) if a else None, "created_at": _iso(ap.created_at)})
    from .dashboard import alerts

    ticker = db.scalars(select(Event).where(Event.tenant_id == tenant_id)
                        .order_by(Event.id.desc()).limit(20)).all()
    raised = db.scalars(select(Event).where(Event.tenant_id == tenant_id,
                                            Event.type == "alert.raised",
                                            Event.ts >= now - timedelta(hours=24))
                        .order_by(Event.id.desc()).limit(20)).all()
    sirens = sorted({e.from_id for e in raised if e.payload.get("kind") == "failure_siren"
                     and e.from_kind == "department" and e.ts >= now - timedelta(minutes=10)})
    last5 = db.scalar(select(func.count()).select_from(Event).where(
        Event.tenant_id == tenant_id, Event.ts >= now - timedelta(minutes=5))) or 0
    last_id = db.scalar(select(func.max(Event.id)).where(Event.tenant_id == tenant_id)) or 0
    agent_rows = [agent_dict(a) for a in agents]
    return {
        "generated_at": now.isoformat(),
        "demo": city.demo_mode(),
        "last_event_id": last_id,
        "jaxon": {"events_per_minute": round(last5 / 5, 2),
                  "agents": [a["id"] for a in agent_rows if a["department_id"] is None]},
        "departments": [{"id": str(d.id), "key": d.key, "name": d.name, "color": d.color,
                         "slot": d.slot, "staffed": d.id in staffed,
                         "floors": [{"id": str(f.id), "key": f.key, "name": f.name,
                                     "level": f.level, "agents": floor_agents.get(f.id, [])}
                                    for f in floors if f.department_id == d.id]}
                        for d in depts],
        "agents": agent_rows,
        "hud": {
            "agents_online": sum(1 for a in agent_rows if a["online"]),
            "agents_total": len(agent_rows),
            "tasks_in_flight": task_count(TaskState.RUNNING.value,
                                          TaskState.WAITING_APPROVAL.value,
                                          TaskState.QUEUED.value),
            "done_today": task_count(TaskState.SUCCEEDED.value, since=today),
            "failed_today": task_count(TaskState.FAILED.value, since=today),
            # SUM() comes back as Decimal once there is real spend: keep plain numbers
            "spend_today_usd": round(int(model_micros) / 1e6 + int(ledger_cents) / 100, 4),
            "revenue_today_usd": revenue,  # null = hidden: no connected revenue source
        },
        "inbox": {"approvals": inbox, "alerts": alerts(db, tenant_id),
                  "letters": [event_dict(e) for e in raised]},
        "sirens": sirens,
        "ticker": [event_dict(e) for e in ticker],
    }


@router.get("/tenants/{tenant_id}/city/state")
def city_state(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    out = snapshot(db, tenant_id)
    db.commit()  # keeps registry rows and any offline marks the snapshot made
    return out


@router.get("/tenants/{tenant_id}/city/events")
def city_events(tenant_id: uuid.UUID, member: TenantMember, db: DB,
                since: Annotated[int, Query(ge=0)] = 0, agent: uuid.UUID | None = None,
                task: uuid.UUID | None = None, run: uuid.UUID | None = None,
                since_ts: datetime | None = None,
                limit: Annotated[int, Query(ge=1, le=1000)] = 200) -> list[dict]:
    """Events after id `since` (or after `since_ts`), oldest first. Filters by agent/task."""
    q = select(Event).where(Event.tenant_id == tenant_id, Event.id > since)
    if agent:
        q = q.where(Event.agent_id == agent)
    if task:
        q = q.where(Event.task_id == task)
    if run:
        q = q.where(Event.run_id == run)
    if since_ts:
        q = q.where(Event.ts >= since_ts)
    return [event_dict(e) for e in db.scalars(q.order_by(Event.id).limit(limit))]


def _stream(tenant_id: uuid.UUID, after: int, timeout: float, poll: float = 0.25,
            keepalive: float = 15.0) -> Iterator[str]:
    """Stream events as they commit. While anyone is watching, the stream also runs the
    offline sweep every HEARTBEAT_S, so a dead worker turns gray with no UI action."""
    maker = make_sessionmaker(load_settings().database_url)
    deadline = time.monotonic() + timeout
    last_sent = last_sweep = time.monotonic()
    yield "retry: 2000\n\n"
    while time.monotonic() < deadline:
        with maker() as s:
            bind_tenant(s, tenant_id)
            if time.monotonic() - last_sweep >= city.HEARTBEAT_S:
                last_sweep = time.monotonic()
                city.sweep_offline(s)
                s.commit()
                bind_tenant(s, tenant_id)
            rows = list(s.scalars(select(Event).where(Event.tenant_id == tenant_id,
                                                      Event.id > after)
                                  .order_by(Event.id).limit(200)))
            out = [event_dict(e) for e in rows]
        for ev in out:
            after = ev["id"]
            yield f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
            last_sent = time.monotonic()
        if not out:
            if time.monotonic() - last_sent >= keepalive:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            time.sleep(poll)


@router.get("/tenants/{tenant_id}/city/stream")
def city_stream(tenant_id: uuid.UUID, member: TenantMember,
                after: Annotated[int, Query(ge=0)] = 0,
                timeout: Annotated[float, Query(gt=0, le=60)] = 25,
                last_event_id: Annotated[str | None, Header()] = None) -> StreamingResponse:
    """Server-Sent Events of the City. Reconnect with Last-Event-ID (or ?after=)."""
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))
    return StreamingResponse(_stream(tenant_id, after, timeout), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- Inside the buildings: one agent's live monitor, Jaxon's routing view (phase 4) -----------

def _get_agent(db: Session, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    a = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    if a is None:
        raise HTTPException(404, "agent not found")
    return a


@router.get("/tenants/{tenant_id}/city/agents/{agent_id}")
def agent_detail(tenant_id: uuid.UUID, agent_id: uuid.UUID, member: TenantMember,
                 db: DB) -> dict:
    """What the agent's desk monitor shows: current task traced back to Steady's command,
    the last 8 log lines, recent tool calls, time on task, today's numbers."""
    a = _get_agent(db, tenant_id, agent_id)
    snap = {x["id"]: x for x in snapshot(db, tenant_id)["agents"]}
    me = snap[str(a.id)]
    task = db.get(Task, a.current_task_id) if a.current_task_id else None
    trace = []
    if task is not None:
        run = db.get(WorkflowRun, task.run_id)
        chain, t = [], task
        while t is not None and len(chain) < 10:
            chain.append({"id": str(t.id), "title": t.title, "kind": t.kind, "status": t.status})
            t = db.get(Task, t.parent_task_id) if t.parent_task_id else None
        trace = [{"command": run.objective, "run_id": str(run.id), "type": run.command_type,
                  "issued_at": _iso(run.created_at)}, *reversed(chain)]
    def last(types, n):
        return [event_dict(e) for e in db.scalars(select(Event).where(
            Event.tenant_id == tenant_id, Event.agent_id == a.id, Event.type.in_(types))
            .order_by(Event.id.desc()).limit(n))]
    started = task.started_at if task else None
    return {**me, "trace": trace,
            "time_on_task_s": int((datetime.now(UTC) - started).total_seconds())
            if started else None,
            "log": last(("agent.log",), 8), "tools": last(("tool.called",), 8),
            "recent": last(("task.completed", "task.failed", "agent.killed",
                            "approval.requested"), 10),
            "controls": {"pause": a.status not in ("paused", "killed"),
                         "resume": a.status in ("paused", "killed"),
                         "kill": a.status != "killed",
                         "reassign": a.key.startswith("spec:") and task is not None
                         and task.status in ("queued", "waiting_approval")}}


@router.get("/tenants/{tenant_id}/city/jaxon")
def jaxon_view(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    """The routing brain: recent commands from Steady, where each one's tasks went, and the
    memory Jaxon looked up for it. All from runs, tasks and events."""
    runs = db.scalars(select(WorkflowRun).where(WorkflowRun.tenant_id == tenant_id)
                      .order_by(WorkflowRun.created_at.desc()).limit(10)).all()
    depts = {d.id: d.name for d in db.scalars(select(Department).where(
        Department.tenant_id == tenant_id))}
    out = []
    for r in runs:
        tasks = db.scalars(select(Task).where(Task.run_id == r.id).order_by(Task.sequence)).all()
        routed = []
        for t in tasks:
            a = city.agent_for_task(db, t)
            routed.append({"task": t.title, "status": t.status, "agent": a.name,
                           "agent_id": str(a.id),
                           "department": depts.get(a.department_id, "Jaxon")})
        reasons = [event_dict(e) for e in db.scalars(select(Event).where(
            Event.run_id == r.id, Event.type == "command.routed").order_by(Event.id))]
        memory = [{"offered": (t.output or {}).get("memory_offered"),
                   "cited": (t.output or {}).get("memory_citations")}
                  for t in tasks if t.kind == "plan" and t.output]
        out.append({"run_id": str(r.id), "command": r.objective, "type": r.command_type,
                    "status": r.status, "at": _iso(r.created_at), "routed_to": routed,
                    "routing_reasons": reasons, "memory": memory})
    db.commit()
    return {"commands": out}


@router.get("/tenants/{tenant_id}/city/trace/{run_id}")
def trace(tenant_id: uuid.UUID, run_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    """Any envelope → its command → Jaxon's routing → every task → the result."""
    run = db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id,
                                              WorkflowRun.tenant_id == tenant_id))
    if run is None:
        raise HTTPException(404, "command not found")
    tasks = db.scalars(select(Task).where(Task.run_id == run.id).order_by(Task.sequence)).all()
    evs = db.scalars(select(Event).where(Event.run_id == run.id).order_by(Event.id)).all()
    cost = sum(float(e.payload.get("usd") or 0) for e in evs if e.type == "cost.recorded")
    steps = []
    for t in tasks:
        a = city.agent_for_task(db, t)
        out = t.output if isinstance(t.output, dict) else {}
        steps.append({"task_id": str(t.id), "title": t.title, "kind": t.kind, "status": t.status,
                      "agent": a.name, "agent_id": str(a.id),
                      "started_at": _iso(t.started_at), "finished_at": _iso(t.finished_at),
                      "result": {k: v for k, v in out.items()
                                 if isinstance(v, (str, int, float, bool))} or None,
                      "error": t.error})
    db.commit()
    return {"command": {"run_id": str(run.id), "text": run.objective, "type": run.command_type,
                        "status": run.status, "issued_at": _iso(run.created_at)},
            "routing": [event_dict(e) for e in evs if e.type == "command.routed"],
            "tasks": steps, "cost_usd": round(cost, 6),
            "approvals": [event_dict(e) for e in evs
                          if e.type in ("approval.requested", "approval.decided")],
            "events": [event_dict(e) for e in evs]}


class LimitIn(BaseModel):
    daily_limit_usd: float | None = Field(default=None, ge=0, le=10_000)


@router.patch("/tenants/{tenant_id}/city/agents/{agent_id}")
def set_limit(tenant_id: uuid.UUID, agent_id: uuid.UUID, body: LimitIn, writer: TenantWriter,
              db: DB) -> dict:
    """The cost guard's daily limit for one agent (null = the default)."""
    from .governance import audit

    _admin(writer)
    a = _get_agent(db, tenant_id, agent_id)
    before = city.daily_limit(a)
    a.daily_limit_usd = body.daily_limit_usd
    db.flush()
    audit(db, tenant_id, writer.user_id, "agent.daily_limit", "Agent", a.id,
          before=before, after=city.daily_limit(a))
    out = {"agent_id": str(a.id), "daily_limit_usd": city.daily_limit(a)}
    db.commit()
    return out


# --- Client view: a read-only, business-scoped City behind a signed link ---------------------

class ShareIn(BaseModel):
    business_id: uuid.UUID
    days: int = Field(default=7, ge=1, le=90)
    label: str = Field(default="", max_length=120)


def _share_key() -> bytes:
    import hashlib
    import os

    secret = os.environ.get("UNSUBSCRIBE_SECRET") or os.environ.get("CITY_SHARE_SECRET") or ""
    if len(secret) < 16:
        raise HTTPException(503, "sharing needs UNSUBSCRIBE_SECRET (set at deploy) to sign links")
    return hashlib.sha256(b"jackson-city-share:" + secret.encode()).digest()


def share_token(tenant_id: uuid.UUID, share_id: uuid.UUID) -> str:
    import base64
    import hashlib
    import hmac

    body = f"{tenant_id}.{share_id}"
    sig = hmac.new(_share_key(), body.encode(), hashlib.sha256).digest()[:18]
    return f"{body}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def read_share_token(token: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    import hmac

    try:
        t, sid, _sig = token.split(".")
        tenant_id, share_id = uuid.UUID(t), uuid.UUID(sid)
    except ValueError:
        return None
    if not hmac.compare_digest(share_token(tenant_id, share_id), token):
        return None
    return tenant_id, share_id


@router.post("/tenants/{tenant_id}/city/shares", status_code=201)
def create_share(tenant_id: uuid.UUID, body: ShareIn, writer: TenantWriter, db: DB) -> dict:
    import os

    from .governance import audit
    from .models import Business, CityShare

    _admin(writer)
    b = db.get(Business, body.business_id)
    if b is None or b.tenant_id != tenant_id:
        raise HTTPException(404, "business not found")
    sh = CityShare(tenant_id=tenant_id, business_id=b.id, label=body.label or b.name,
                   created_by=writer.user_id,
                   expires_at=datetime.now(UTC) + timedelta(days=body.days))
    db.add(sh)
    db.flush()
    token = share_token(tenant_id, sh.id)
    audit(db, tenant_id, writer.user_id, "city.share.created", "CityShare", sh.id,
          business_id=str(b.id), days=body.days)
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    out = {"id": str(sh.id), "business": b.name, "label": sh.label,
           "expires_at": _iso(sh.expires_at), "url": f"{base}/city?share={token}",
           "token": token}
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/city/shares")
def list_shares(tenant_id: uuid.UUID, writer: TenantWriter, db: DB) -> list[dict]:
    from .models import CityShare

    _admin(writer)
    return [{"id": str(s.id), "business_id": str(s.business_id), "label": s.label,
             "expires_at": _iso(s.expires_at), "revoked": s.revoked_at is not None}
            for s in db.scalars(select(CityShare).where(CityShare.tenant_id == tenant_id)
                                .order_by(CityShare.created_at.desc()))]


@router.post("/tenants/{tenant_id}/city/shares/{share_id}/revoke")
def revoke_share(tenant_id: uuid.UUID, share_id: uuid.UUID, writer: TenantWriter,
                 db: DB) -> dict:
    from .governance import audit
    from .models import CityShare

    _admin(writer)
    sh = db.scalar(select(CityShare).where(CityShare.id == share_id,
                                           CityShare.tenant_id == tenant_id))
    if sh is None:
        raise HTTPException(404, "share not found")
    sh.revoked_at = sh.revoked_at or datetime.now(UTC)
    audit(db, tenant_id, writer.user_id, "city.share.revoked", "CityShare", sh.id)
    db.commit()
    return {"id": str(share_id), "revoked": True}


def client_snapshot(db: Session, tenant_id: uuid.UUID, business_id: uuid.UUID) -> dict:
    """Only this client's work: statuses come from this business's tasks, events from its
    runs. No spend, no inbox, no other client, no controls."""
    from .models import Business

    today = _day_start()
    biz = db.get(Business, business_id)
    run_ids = select(WorkflowRun.id).where(WorkflowRun.tenant_id == tenant_id,
                                           WorkflowRun.business_id == business_id)
    tasks = db.scalars(select(Task).where(Task.run_id.in_(run_ids))).all()
    live: dict = {}
    for t in tasks:
        if t.status in (TaskState.RUNNING.value, TaskState.WAITING_APPROVAL.value):
            live[city.agent_for_task(db, t).id] = (
                "working" if t.status == TaskState.RUNNING.value else "waiting", t.title)
    depts = db.scalars(select(Department).where(Department.tenant_id == tenant_id)
                       .order_by(Department.slot)).all()
    floors = db.scalars(select(Floor).where(Floor.tenant_id == tenant_id)
                        .order_by(Floor.level)).all()
    agents = db.scalars(select(Agent).where(Agent.tenant_id == tenant_id)
                        .order_by(Agent.name)).all()
    staffed = {a.department_id for a in agents if a.department_id}
    fa: dict = {}
    for a in agents:
        if a.floor_id:
            fa.setdefault(a.floor_id, []).append(str(a.id))
    evs = db.scalars(select(Event).where(Event.tenant_id == tenant_id, Event.run_id.in_(run_ids),
                                         Event.type.in_(("command.issued", "task.created",
                                                         "task.started", "task.completed",
                                                         "task.failed", "command.routed")))
                     .order_by(Event.id.desc()).limit(20)).all()
    safe = ("title", "kind", "agent", "text")
    return {
        "generated_at": datetime.now(UTC).isoformat(), "demo": city.demo_mode(),
        "client": {"business": biz.name if biz else "", "read_only": True},
        "last_event_id": evs[0].id if evs else 0,
        "jaxon": {"events_per_minute": 0, "agents": [str(a.id) for a in agents
                                                     if not a.department_id]},
        "departments": [{"id": str(d.id), "key": d.key, "name": d.name, "color": d.color,
                         "slot": d.slot, "staffed": d.id in staffed,
                         "floors": [{"id": str(f.id), "key": f.key, "name": f.name,
                                     "level": f.level, "agents": fa.get(f.id, [])}
                                    for f in floors if f.department_id == d.id]}
                        for d in depts],
        "agents": [{"id": str(a.id), "name": a.name, "role": a.role, "key": a.key,
                    "department_id": str(a.department_id) if a.department_id else None,
                    "floor_id": str(a.floor_id) if a.floor_id else None,
                    "status": live.get(a.id, ("idle", None))[0], "online": True,
                    "model": None, "last_heartbeat_at": None, "status_changed_at": None,
                    "current_task": {"id": "", "title": live[a.id][1], "status": live[a.id][0],
                                     "started_at": None, "run_id": "", "command": None}
                    if a.id in live else None,
                    "cost_today_usd": 0, "daily_limit_usd": 0, "done_today": 0,
                    "failed_today": 0, "success_rate_7d": None} for a in agents],
        "hud": {"agents_online": len(live), "agents_total": len(agents),
                "tasks_in_flight": sum(1 for t in tasks if t.status in (
                    "running", "waiting_approval", "queued")),
                "done_today": sum(1 for t in tasks if t.status == "succeeded"
                                  and t.finished_at and t.finished_at >= today),
                "failed_today": sum(1 for t in tasks if t.status == "failed"
                                    and t.finished_at and t.finished_at >= today),
                "spend_today_usd": 0, "revenue_today_usd": None},
        "inbox": {"approvals": [], "alerts": [], "letters": []}, "sirens": [],
        "ticker": [{**event_dict(e), "payload": {k: v for k, v in e.payload.items()
                                                 if k in safe}} for e in evs],
    }


@router.get("/city/share/{token}/state", include_in_schema=False)
def shared_state(token: str, db: DB) -> dict:
    """Public, read-only: the signed token is the credential; revocable and expiring."""
    from .models import CityShare

    parsed = read_share_token(token)
    if parsed is None:
        raise HTTPException(404, "link not valid")
    tenant_id, share_id = parsed
    bind_tenant(db, tenant_id)
    sh = db.get(CityShare, share_id)
    if sh is None or sh.revoked_at is not None or sh.expires_at <= datetime.now(UTC):
        raise HTTPException(404, "link not valid or expired")
    out = client_snapshot(db, tenant_id, sh.business_id)
    out["client"]["label"] = sh.label
    out["client"]["expires_at"] = _iso(sh.expires_at)
    db.rollback()  # read-only
    return out


# --- Controls: they act first, and only then is the event emitted --------------------------

def _admin(m) -> None:
    if m.role not in ("owner", "admin"):
        raise HTTPException(403, "only the owner or an admin can control agents")


@router.post("/tenants/{tenant_id}/city/agents/{agent_id}/{action}")
def control_agent(tenant_id: uuid.UUID, agent_id: uuid.UUID, action: str,
                  writer: TenantWriter, db: DB, body: dict | None = None) -> dict:
    """pause: no new work, and its current run is paused (the worker stops at its next
    check). resume: lifts a pause or kill. kill: the worker terminates the task's process,
    records it, then emits agent.killed. reassign: moves a waiting task of an AgentSpec
    agent to another active AgentSpec agent."""
    from .governance import audit
    from .models import AgentSpec

    _admin(writer)
    a = _get_agent(db, tenant_id, agent_id)
    task = db.get(Task, a.current_task_id) if a.current_task_id else None
    running = task is not None and task.status == TaskState.RUNNING.value
    now = datetime.now(UTC)
    if action == "pause":
        if a.status in ("paused", "killed"):
            raise HTTPException(409, f"agent is already {a.status}")
        a.status, a.status_changed_at = "paused", now
        paused_run = None
        if task is not None and task.status not in ("succeeded", "failed", "canceled"):
            run = db.get(WorkflowRun, task.run_id)
            if not run.paused and run.status not in ("succeeded", "failed", "canceled"):
                run.paused, paused_run = True, str(run.id)
        db.flush()  # the hold is in place before anyone hears about it
        city.emit(db, tenant_id, "agent.paused", from_=("ceo", "steady"), agent_id=a.id,
                  task_id=task.id if task else None,
                  payload={"paused_run": paused_run, "by": str(writer.user_id)})
    elif action == "resume":
        if a.status not in ("paused", "killed"):
            raise HTTPException(409, f"agent is {a.status}, not paused or killed")
        was = a.status
        a.status, a.status_changed_at = "idle", now
        if task is not None and was == "paused":
            run = db.get(WorkflowRun, task.run_id)
            if run.paused:
                run.paused = False
        db.flush()
        city.emit(db, tenant_id, "agent.resumed", from_=("ceo", "steady"), agent_id=a.id,
                  payload={"was": was, "by": str(writer.user_id)})
    elif action == "kill":
        if a.status == "killed":
            raise HTTPException(409, "agent is already killed")
        a.status, a.status_changed_at = "killed", now
        if not running:  # nothing is executing: the hold itself is the stop
            db.flush()
            city.emit(db, tenant_id, "agent.killed", from_=("ceo", "steady"), agent_id=a.id,
                      payload={"stopped": "no task was running", "by": str(writer.user_id)})
        # running: the worker sees the hold, terminates the process, then emits agent.killed
    elif action == "reassign":
        to = (body or {}).get("to_agent_id")
        if not a.key.startswith("spec:"):
            raise HTTPException(409, "built-in workers each do one kind of job; there is "
                                     "nobody to reassign this task to")
        if task is None or task.status not in ("queued", "waiting_approval"):
            raise HTTPException(409, "only a waiting or queued task can be reassigned")
        target = _get_agent(db, tenant_id, uuid.UUID(str(to))) if to else None
        if target is None or not target.key.startswith("spec:") or target.id == a.id:
            raise HTTPException(422, "pick another AgentSpec agent")
        spec = db.scalar(select(AgentSpec).where(AgentSpec.tenant_id == tenant_id,
                                                 AgentSpec.name == target.key[5:],
                                                 AgentSpec.status == "active"))
        if spec is None:
            raise HTTPException(409, "that agent has no active spec")
        task.agent_spec_id = spec.id
        a.current_task_id = None
        db.flush()
        city.emit(db, tenant_id, "agent.log", agent_id=a.id, task_id=task.id,
                  payload={"line": f"task reassigned to {target.name} by Steady"})
        city.emit(db, tenant_id, "task.created", from_=("jaxon", "jaxon"),
                  to=city.address(target), task_id=task.id, run_id=task.run_id,
                  agent_id=target.id, payload={"title": task.title[:200], "reassigned": True})
    else:
        raise HTTPException(404, "unknown control")
    audit(db, tenant_id, writer.user_id, f"agent.{action}", "Agent", a.id,
          running=running, task_id=str(task.id) if task else None)
    out = {"agent_id": str(a.id), "action": action, "status": a.status,
           "pending_stop": action == "kill" and running}
    db.commit()
    return out


# --- The City page (built from apps/city; static, no data in it) ------------------------------

CITY = Path(__file__).parent / "city_dist"


def _page_headers(cache: str) -> dict:
    from .routes_dashboard import _csp

    return {"Content-Security-Policy": _csp(), "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Cache-Control": cache}


@router.get("/city", include_in_schema=False)
def city_page() -> Response:
    """The 3D City. Same sign-in and strict CSP as the console; it reads everything
    through the authenticated API above."""
    return Response((CITY / "index.html").read_text(), media_type="text/html; charset=utf-8",
                    headers=_page_headers("no-store"))


@router.get("/city/assets/{name}", include_in_schema=False)
def city_asset(name: str) -> Response:
    path = CITY / "assets" / name
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.(js|css)", name) or not path.is_file():
        raise HTTPException(404, "not found")
    media = "text/javascript" if name.endswith(".js") else "text/css"
    return Response(path.read_bytes(), media_type=media,
                    headers=_page_headers("public, max-age=31536000, immutable"))
