"""Process-wide runtime: wires settings, storage, providers, skills and memory together."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from jaxon.agents.executor import AgentExecutor
from jaxon.core.config import Settings
from jaxon.db.session import Database
from jaxon.memory.embeddings import HashingEmbedder
from jaxon.memory.store import MemoryStore
from jaxon.providers.base import ModelProvider
from jaxon.providers.registry import create_provider
from jaxon.queue.redis_queue import RunQueue
from jaxon.skills.base import SkillContext
from jaxon.skills.registry import SkillRegistry, build_default_registry


@dataclass
class Runtime:
    settings: Settings
    db: Database
    redis: Redis
    queue: RunQueue
    skills: SkillRegistry
    provider: ModelProvider
    memory: MemoryStore

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        provider: ModelProvider | None = None,
        skills: SkillRegistry | None = None,
    ) -> Runtime:
        db = Database(settings)
        redis = Redis.from_url(settings.redis_url)
        return cls(
            settings=settings,
            db=db,
            redis=redis,
            queue=RunQueue(redis, settings.run_queue_name),
            skills=skills
            or build_default_registry(settings.skill_packages, settings.skill_timeout_seconds),
            provider=provider or create_provider(settings),
            memory=MemoryStore(db.sessionmaker, HashingEmbedder(settings.embedding_dim)),
        )

    def executor(self) -> AgentExecutor:
        return AgentExecutor(
            self.provider,
            self.skills,
            default_model=self.settings.default_model,
            default_effort=self.settings.model_effort,
            max_tokens=self.settings.model_max_tokens,
        )

    def skill_context(self, **kw: object) -> SkillContext:
        return SkillContext(settings=self.settings, memory=self.memory, **kw)  # type: ignore[arg-type]

    async def aclose(self) -> None:
        await self.provider.aclose()
        await self.redis.aclose()
        await self.db.dispose()
