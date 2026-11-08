from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response

from jaxon.api.deps import get_runtime
from jaxon.api.schemas import MemoryCreate, MemorySearch
from jaxon.core.errors import NotFoundError
from jaxon.runtime import Runtime

router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("", status_code=201)
async def add_memory(body: MemoryCreate, rt: Runtime = Depends(get_runtime)) -> dict[str, str]:
    mid = await rt.memory.add(body.content, namespace=body.namespace, metadata=body.metadata)
    return {"id": str(mid)}


@router.post("/search")
async def search_memory(body: MemorySearch, rt: Runtime = Depends(get_runtime)) -> dict[str, Any]:
    hits = await rt.memory.search(body.query, namespace=body.namespace, limit=body.limit)
    return {
        "hits": [
            {
                "id": str(h.id),
                "content": h.content,
                "score": round(h.score, 4),
                "metadata": h.metadata,
                "namespace": h.namespace,
            }
            for h in hits
        ]
    }


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: uuid.UUID, rt: Runtime = Depends(get_runtime)) -> Response:
    if not await rt.memory.delete(memory_id):
        raise NotFoundError(f"Memory {memory_id} not found")
    return Response(status_code=204)
