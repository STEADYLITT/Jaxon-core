"""Reliable-ish Redis work queue (BLMOVE into a per-worker processing list)."""

from __future__ import annotations

import uuid

from redis.asyncio import Redis


class RunQueue:
    def __init__(self, redis: Redis, name: str) -> None:
        self.redis = redis
        self.name = name

    def _processing(self, worker_id: str) -> str:
        return f"{self.name}:processing:{worker_id}"

    async def enqueue(self, run_id: uuid.UUID) -> None:
        await self.redis.lpush(self.name, str(run_id))

    async def dequeue(self, worker_id: str, block_seconds: float = 5.0) -> uuid.UUID | None:
        raw = await self.redis.blmove(
            self.name, self._processing(worker_id), max(1, round(block_seconds)), "RIGHT", "LEFT"
        )
        if raw is None:
            return None
        return uuid.UUID(raw.decode() if isinstance(raw, bytes) else raw)

    async def ack(self, worker_id: str, run_id: uuid.UUID) -> None:
        await self.redis.lrem(self._processing(worker_id), 1, str(run_id))

    async def recover(self, worker_id: str) -> int:
        """Requeue jobs this worker had in flight when it last stopped."""
        n = 0
        while await self.redis.lmove(self._processing(worker_id), self.name, "RIGHT", "LEFT"):
            n += 1
        return n

    async def depth(self) -> int:
        return int(await self.redis.llen(self.name))
