from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from jaxon import __version__
from jaxon.api.deps import get_runtime
from jaxon.integrations import integration_statuses
from jaxon.providers.registry import available_providers
from jaxon.runtime import Runtime

router = APIRouter(tags=["system"])


@router.get("/system")
async def system_info(rt: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    try:
        depth: int | None = await rt.queue.depth()
    except Exception:
        depth = None
    s = rt.settings
    return {
        "name": s.app_name,
        "version": __version__,
        "environment": s.environment,
        "model_provider": rt.provider.name,
        "available_providers": available_providers(),
        "default_model": s.default_model,
        "skills": len(rt.skills),
        "queue_depth": depth,
        "integrations": [
            i.model_dump() for i in integration_statuses(s, rt.provider.is_configured())
        ],
    }
