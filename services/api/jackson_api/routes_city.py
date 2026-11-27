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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import city
from .contracts import TaskState
from .db import bind_tenant, make_sessionmaker
from .deps import DB, TenantMember
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
    depts = db.scalars(select(Department).where(Department.tenant_id == tenant_id)
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
        revenue = (db.scalar(select(func.coalesce(func.sum(RevenueEntry.amount_cents), 0))
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
            "spend_today_usd": round(model_micros / 1e6 + ledger_cents / 100, 4),
            "revenue_today_usd": revenue,  # null = hidden: no connected revenue source
        },
        "inbox": {"approvals": inbox, "alerts": alerts(db, tenant_id)},
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
