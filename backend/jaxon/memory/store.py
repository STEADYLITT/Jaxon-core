"""pgvector-backed long-term memory."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jaxon.core.errors import ValidationFailed
from jaxon.db.models import EMBEDDING_DIM, Memory
from jaxon.memory.embeddings import EmbeddingProvider


@dataclass(slots=True)
class MemoryHit:
    id: uuid.UUID
    namespace: str
    content: str
    metadata: dict[str, Any]
    score: float  # cosine similarity in [-1, 1]


class MemoryStore:
    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], embedder: EmbeddingProvider
    ) -> None:
        if embedder.dim != EMBEDDING_DIM:
            raise ValueError(f"Embedder dim {embedder.dim} != database vector dim {EMBEDDING_DIM}")
        self._sm = sessionmaker
        self._embedder = embedder

    async def add(
        self, content: str, *, namespace: str = "default", metadata: dict[str, Any] | None = None
    ) -> uuid.UUID:
        if not content.strip():
            raise ValidationFailed("Memory content must not be empty")
        [vec] = await self._embedder.embed([content])
        item = Memory(namespace=namespace, content=content, meta=metadata or {}, embedding=vec)
        async with self._sm() as s:
            s.add(item)
            await s.commit()
            return item.id

    async def search(
        self, query: str, *, namespace: str = "default", limit: int = 5
    ) -> list[MemoryHit]:
        [vec] = await self._embedder.embed([query])
        distance = Memory.embedding.cosine_distance(vec).label("distance")
        stmt = (
            select(Memory, distance)
            .where(Memory.namespace == namespace)
            .order_by(distance)
            .limit(max(1, min(limit, 50)))
        )
        async with self._sm() as s:
            rows = (await s.execute(stmt)).all()
        return [
            MemoryHit(
                id=m.id,
                namespace=m.namespace,
                content=m.content,
                metadata=m.meta,
                score=1.0 - float(d),
            )
            for m, d in rows
        ]

    async def delete(self, memory_id: uuid.UUID) -> bool:
        async with self._sm() as s:
            item = await s.get(Memory, memory_id)
            if item is None:
                return False
            await s.delete(item)
            await s.commit()
            return True
