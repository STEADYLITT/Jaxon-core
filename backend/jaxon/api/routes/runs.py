from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from jaxon.api.deps import get_runtime
from jaxon.api.schemas import CreateRunRequest, RunDetailOut, RunListOut, RunOut
from jaxon.runtime import Runtime
from jaxon.services import runs as svc

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", response_model=RunDetailOut, status_code=201)
async def create_run(body: CreateRunRequest, rt: Runtime = Depends(get_runtime)) -> RunDetailOut:
    run = await svc.create_run(rt, body.agent, body.input)
    if body.mode == "sync":
        run = await svc.execute_run(rt, run.id)
    else:
        await rt.queue.enqueue(run.id)
    return RunDetailOut.model_validate(run)


@router.get("", response_model=RunListOut)
async def list_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    rt: Runtime = Depends(get_runtime),
) -> RunListOut:
    items, total = await svc.list_runs(rt, limit, offset)
    return RunListOut(items=[RunOut.model_validate(r) for r in items], total=total)


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run(run_id: uuid.UUID, rt: Runtime = Depends(get_runtime)) -> RunDetailOut:
    return RunDetailOut.model_validate(await svc.get_run(rt, run_id))
