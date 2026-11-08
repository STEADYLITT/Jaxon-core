from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from jaxon.agents.base import AgentSpec
from jaxon.api.deps import get_runtime, get_session
from jaxon.runtime import Runtime
from jaxon.services import agents as svc

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentSpec])
async def list_agents(session: AsyncSession = Depends(get_session)) -> list[AgentSpec]:
    return await svc.list_agents(session)


@router.get("/{name}", response_model=AgentSpec)
async def get_agent(name: str, session: AsyncSession = Depends(get_session)) -> AgentSpec:
    return await svc.resolve_agent(session, name)


@router.post("", response_model=AgentSpec, status_code=201)
async def create_agent(
    spec: AgentSpec,
    session: AsyncSession = Depends(get_session),
    rt: Runtime = Depends(get_runtime),
) -> AgentSpec:
    return await svc.create_agent(session, spec.model_copy(update={"builtin": False}), rt.skills)


@router.delete("/{name}", status_code=204)
async def delete_agent(name: str, session: AsyncSession = Depends(get_session)) -> Response:
    await svc.delete_agent(session, name)
    return Response(status_code=204)
