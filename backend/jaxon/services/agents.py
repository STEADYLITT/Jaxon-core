from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jaxon.agents.base import AgentSpec
from jaxon.agents.builtin import BUILTIN_AGENTS
from jaxon.core.errors import ConflictError, NotFoundError, ValidationFailed
from jaxon.db.models import AgentDefinition
from jaxon.skills.registry import SkillRegistry


def _to_spec(row: AgentDefinition) -> AgentSpec:
    return AgentSpec(
        name=row.name,
        description=row.description,
        system_prompt=row.system_prompt,
        skills=list(row.skills),
        model=row.model,
        effort=row.effort,
        max_steps=row.max_steps,
    )


async def list_agents(session: AsyncSession) -> list[AgentSpec]:
    rows = (await session.scalars(select(AgentDefinition).order_by(AgentDefinition.name))).all()
    return [*BUILTIN_AGENTS.values(), *(_to_spec(r) for r in rows)]


async def resolve_agent(session: AsyncSession, name: str) -> AgentSpec:
    if name in BUILTIN_AGENTS:
        return BUILTIN_AGENTS[name]
    row = await session.scalar(select(AgentDefinition).where(AgentDefinition.name == name))
    if row is None:
        raise NotFoundError(f"Unknown agent '{name}'")
    return _to_spec(row)


async def create_agent(session: AsyncSession, spec: AgentSpec, skills: SkillRegistry) -> AgentSpec:
    if spec.name in BUILTIN_AGENTS:
        raise ConflictError(f"'{spec.name}' is a built-in agent name")
    missing = [s for s in spec.skills if s not in skills]
    if missing:
        raise ValidationFailed("Unknown skills", details={"missing": missing})
    row = AgentDefinition(
        name=spec.name,
        description=spec.description,
        system_prompt=spec.system_prompt,
        model=spec.model,
        effort=spec.effort,
        skills=spec.skills,
        max_steps=spec.max_steps,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ConflictError(f"Agent '{spec.name}' already exists") from None
    return _to_spec(row)


async def delete_agent(session: AsyncSession, name: str) -> None:
    row = await session.scalar(select(AgentDefinition).where(AgentDefinition.name == name))
    if row is None:
        raise NotFoundError(f"Unknown agent '{name}'")
    await session.delete(row)
    await session.commit()
