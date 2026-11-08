"""Run lifecycle: create -> (queue) -> execute -> persist steps and result."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select

from jaxon.agents.executor import StepEvent
from jaxon.core.errors import NotFoundError
from jaxon.core.logging import bind_context, get_logger
from jaxon.db.base import utcnow
from jaxon.db.models import AgentRun, RunStatus, RunStep
from jaxon.runtime import Runtime
from jaxon.services.agents import resolve_agent

log = get_logger(__name__)


async def create_run(rt: Runtime, agent_name: str, user_input: str) -> AgentRun:
    async with rt.db.sessionmaker() as s:
        await resolve_agent(s, agent_name)  # fail fast on unknown agent
        run = AgentRun(agent_name=agent_name, input=user_input, status=RunStatus.queued, usage={})
        s.add(run)
        await s.commit()
        await s.refresh(run, ["steps"])
        return run


async def get_run(rt: Runtime, run_id: uuid.UUID) -> AgentRun:
    async with rt.db.sessionmaker() as s:
        run = await s.get(AgentRun, run_id)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")
        return run


async def list_runs(rt: Runtime, limit: int = 50, offset: int = 0) -> tuple[list[AgentRun], int]:
    async with rt.db.sessionmaker() as s:
        total = await s.scalar(select(func.count()).select_from(AgentRun)) or 0
        rows = (
            await s.scalars(
                select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)
            )
        ).all()
        return list(rows), int(total)


async def execute_run(rt: Runtime, run_id: uuid.UUID) -> AgentRun:
    """Execute a queued run. Idempotent: runs not in `queued` state are left untouched."""
    bind_context(run_id=str(run_id))
    async with rt.db.sessionmaker() as s:
        run = await s.get(AgentRun, run_id, with_for_update=True)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")
        if run.status != RunStatus.queued:
            log.warning("run.skip_not_queued", status=run.status.value)
            return run
        run.status = RunStatus.running
        run.started_at = utcnow()
        await s.commit()
        try:
            spec = await resolve_agent(s, run.agent_name)
        except NotFoundError as e:
            return await _fail(rt, run_id, e.to_dict())
        user_input = run.input

    async def on_step(ev: StepEvent) -> None:
        async with rt.db.sessionmaker() as s2:
            s2.add(RunStep(run_id=run_id, index=ev.index, kind=ev.kind, payload=ev.payload))
            await s2.commit()

    ctx = rt.skill_context(run_id=run_id, agent_name=spec.name)
    try:
        result = await rt.executor().run(spec, user_input, ctx, on_step=on_step)
    except Exception as e:
        log.exception("run.crashed")
        return await _fail(
            rt, run_id, {"code": "internal_error", "message": f"{type(e).__name__}: {e}"}
        )
    finally:
        if ctx.http is not None:
            await ctx.http.aclose()

    async with rt.db.sessionmaker() as s:
        run = await s.get(AgentRun, run_id)
        assert run is not None
        run.status = RunStatus.succeeded if result.status == "succeeded" else RunStatus.failed
        run.output = result.output
        run.error = result.error
        run.model = result.model
        run.stop_reason = result.stop_reason
        run.usage = result.usage.model_dump()
        run.finished_at = utcnow()
        await s.commit()
        await s.refresh(run, ["steps"])
        log.info("run.finished", status=run.status.value, stop_reason=run.stop_reason)
        return run


async def _fail(rt: Runtime, run_id: uuid.UUID, error: dict[str, Any]) -> AgentRun:
    async with rt.db.sessionmaker() as s:
        run = await s.get(AgentRun, run_id)
        assert run is not None
        run.status = RunStatus.failed
        run.error = error
        run.finished_at = utcnow()
        await s.commit()
        await s.refresh(run, ["steps"])
        return run
