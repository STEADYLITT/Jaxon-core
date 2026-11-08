from __future__ import annotations

from fastapi import APIRouter, Depends

from jaxon.api.deps import get_runtime
from jaxon.api.schemas import InvokeSkillRequest
from jaxon.runtime import Runtime
from jaxon.skills.base import SkillManifest
from jaxon.skills.registry import SkillResult

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=list[SkillManifest])
async def list_skills(
    tag: str | None = None, rt: Runtime = Depends(get_runtime)
) -> list[SkillManifest]:
    return rt.skills.manifests(tag)


@router.get("/{name}", response_model=SkillManifest)
async def get_skill(name: str, rt: Runtime = Depends(get_runtime)) -> SkillManifest:
    return type(rt.skills.get(name)).manifest()


@router.post("/{name}/invoke", response_model=SkillResult)
async def invoke_skill(
    name: str, body: InvokeSkillRequest, rt: Runtime = Depends(get_runtime)
) -> SkillResult:
    rt.skills.get(name)  # 404 for unknown skills
    ctx = rt.skill_context(agent_name="api")
    try:
        return await rt.skills.invoke(name, body.arguments, ctx, approved=body.approved)
    finally:
        if ctx.http is not None:
            await ctx.http.aclose()
