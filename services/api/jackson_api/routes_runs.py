"""Command + run API (spec vol. 02): commands, runs, cancel, SSE events.

All routes are tenant-scoped: /tenants/{tenant_id}/... (ADR-013). Browsers can't
attach an Authorization header to EventSource, so the console will reach
GET .../events through a server-side proxy (as PR #1's dashboard did).
"""

import json
import time
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .contracts import CommandType
from .db import bind_tenant, make_sessionmaker
from .deps import DB, TenantMember, TenantWriter
from .models import RunEvent, Task, WorkflowRun
from .runs import CONTROL, CommandError, control_run, create_run
from .settings import load_settings

router = APIRouter()


class CommandIn(BaseModel):
    type: CommandType
    idempotency_key: str = Field(min_length=8, max_length=200)
    objective: str | None = Field(default=None, max_length=5000)
    business_id: uuid.UUID | None = None
    budget_cents: int | None = Field(default=None, ge=0)
    deadline: datetime | None = None
    constraints: list[str] = Field(default_factory=list, max_length=50)
    success_criteria: list[str] = Field(default_factory=list, max_length=50)
    target_run_id: uuid.UUID | None = None  # PAUSE / RESUME / STOP only
    # Phase 23: from the City's command bar. After planning, Jaxon routes the command to the
    # department agent that can do it and records why (command.routed).
    route: bool = False


class TaskOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    sequence: int
    kind: str
    title: str
    status: str
    created_at: datetime


class RunOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    tenant_id: uuid.UUID
    business_id: uuid.UUID | None
    command_type: str
    objective: str
    budget_cents: int | None
    deadline: datetime | None
    constraints: list[str]
    success_criteria: list[str]
    idempotency_key: str
    status: str
    paused: bool
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    tasks: list[TaskOut] = []


def _error(e: CommandError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": e.message})


def _run_out(db, run: WorkflowRun) -> RunOut:
    out = RunOut.model_validate(run)
    out.tasks = [TaskOut.model_validate(t) for t in db.scalars(
        select(Task).where(Task.run_id == run.id).order_by(Task.sequence))]
    return out


def _get_run(db, tenant_id: uuid.UUID, run_id: uuid.UUID) -> WorkflowRun:
    run = db.scalar(select(WorkflowRun).where(
        WorkflowRun.id == run_id, WorkflowRun.tenant_id == tenant_id))
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@router.post("/tenants/{tenant_id}/commands", response_model=RunOut)
def post_command(tenant_id: uuid.UUID, body: CommandIn, writer: TenantWriter, db: DB):
    try:
        if body.type in CONTROL:
            if body.target_run_id is None:
                raise CommandError(422, "target_run_required", f"{body.type} needs target_run_id")
            run = control_run(db, _get_run(db, tenant_id, body.target_run_id), body.type,
                              writer.user_id)
            status = 200
        else:
            payload = body.model_dump(exclude={"target_run_id"} | (
                set() if body.route else {"route"}))  # unchanged idempotency hashes
            run, created = create_run(db, tenant_id, writer.user_id, payload, announce=True)
            status = 201 if created else 200
    except CommandError as e:
        db.rollback()
        raise _error(e) from None
    db.flush()
    db.refresh(run)  # before commit: tenant scope ends with the transaction (see routes.py)
    out = _run_out(db, run)
    db.commit()
    headers = {"Idempotent-Replay": "true"} if status == 200 and body.type not in CONTROL else {}
    return JSONResponse(out.model_dump(mode="json"), status_code=status, headers=headers)


@router.get("/tenants/{tenant_id}/runs", response_model=list[RunOut])
def list_runs(tenant_id: uuid.UUID, member: TenantMember, db: DB,
              limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[RunOut]:
    runs = db.scalars(select(WorkflowRun).where(WorkflowRun.tenant_id == tenant_id)
                      .order_by(WorkflowRun.created_at.desc()).limit(limit))
    return [RunOut.model_validate(r) for r in runs]


@router.get("/tenants/{tenant_id}/runs/{run_id}", response_model=RunOut)
def get_run(tenant_id: uuid.UUID, run_id: uuid.UUID, member: TenantMember, db: DB) -> RunOut:
    return _run_out(db, _get_run(db, tenant_id, run_id))


@router.post("/tenants/{tenant_id}/runs/{run_id}/cancel", response_model=RunOut)
def cancel_run(tenant_id: uuid.UUID, run_id: uuid.UUID, writer: TenantWriter, db: DB) -> RunOut:
    try:
        run = control_run(db, _get_run(db, tenant_id, run_id), CommandType.STOP, writer.user_id)
    except CommandError as e:
        db.rollback()
        raise _error(e) from None
    db.flush()
    db.refresh(run)
    out = _run_out(db, run)
    db.commit()
    return out


def _sse(tenant_id: uuid.UUID, run_id: uuid.UUID | None, after: int, timeout: float,
         poll: float = 0.25, keepalive: float = 15.0) -> Iterator[str]:
    """Poll run_events and stream them. Each poll is its own tenant-bound transaction."""
    maker = make_sessionmaker(load_settings().database_url)
    deadline = time.monotonic() + timeout
    last_sent = time.monotonic()
    yield "retry: 2000\n\n"
    while time.monotonic() < deadline:
        with maker() as s:
            bind_tenant(s, tenant_id)
            q = select(RunEvent).where(RunEvent.tenant_id == tenant_id, RunEvent.id > after)
            if run_id is not None:
                q = q.where(RunEvent.run_id == run_id)
            events = list(s.scalars(q.order_by(RunEvent.id).limit(100)))
        for ev in events:
            after = ev.id
            data = {"run_id": str(ev.run_id), "type": ev.type, "data": ev.data,
                    "created_at": ev.created_at.isoformat()}
            yield f"id: {ev.id}\nevent: {ev.type}\ndata: {json.dumps(data)}\n\n"
            last_sent = time.monotonic()
        if not events:
            if time.monotonic() - last_sent >= keepalive:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            time.sleep(poll)


@router.get("/tenants/{tenant_id}/events")
def events(
    tenant_id: uuid.UUID,
    member: TenantMember,
    run_id: uuid.UUID | None = None,
    after: Annotated[int, Query(ge=0)] = 0,
    timeout: Annotated[float, Query(gt=0, le=60)] = 25,
    last_event_id: Annotated[str | None, Header()] = None,
) -> Response:
    """Server-Sent Events. Streams for `timeout` seconds; clients reconnect with Last-Event-ID."""
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))
    return StreamingResponse(
        _sse(tenant_id, run_id, after, timeout),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
