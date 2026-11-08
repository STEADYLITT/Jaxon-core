from __future__ import annotations

from typing import Any

from pydantic import Field

from jaxon.core.errors import DependencyUnavailable
from jaxon.skills.base import Permission, SkillContext, SkillInput, SkillOutput, skill


def _store(ctx: SkillContext) -> Any:
    if ctx.memory is None:
        raise DependencyUnavailable("Memory store is not available in this context")
    return ctx.memory


class RememberIn(SkillInput):
    content: str = Field(min_length=1, max_length=20_000)
    namespace: str = Field(default="default", max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RememberOut(SkillOutput):
    id: str


@skill(
    name="memory.remember",
    description="Save a fact or note to long-term memory.",
    tags=("memory",),
    permissions=(Permission.memory_write,),
)
async def remember(inp: RememberIn, ctx: SkillContext) -> RememberOut:
    meta = {**inp.metadata, "agent": ctx.agent_name, "run_id": str(ctx.run_id or "")}
    mid = await _store(ctx).add(inp.content, namespace=inp.namespace, metadata=meta)
    return RememberOut(id=str(mid))


class RecallIn(SkillInput):
    query: str = Field(min_length=1, max_length=2_000)
    namespace: str = Field(default="default", max_length=100)
    limit: int = Field(default=5, ge=1, le=50)


class RecallHit(SkillOutput):
    id: str
    content: str
    score: float
    metadata: dict[str, Any]


class RecallOut(SkillOutput):
    hits: list[RecallHit]


@skill(
    name="memory.recall",
    description="Search long-term memory for relevant notes.",
    tags=("memory",),
    permissions=(Permission.memory_read,),
)
async def recall(inp: RecallIn, ctx: SkillContext) -> RecallOut:
    hits = await _store(ctx).search(inp.query, namespace=inp.namespace, limit=inp.limit)
    return RecallOut(
        hits=[
            RecallHit(id=str(h.id), content=h.content, score=round(h.score, 4), metadata=h.metadata)
            for h in hits
        ]
    )
