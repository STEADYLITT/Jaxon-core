from __future__ import annotations

import os

import pytest

from jaxon.core.config import Settings
from jaxon.skills.base import SkillContext
from jaxon.skills.registry import SkillRegistry, build_default_registry


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        log_json=False,
        anthropic_api_key=None,
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql+asyncpg://jaxon:jaxon@localhost:5432/jaxon"
        ),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/15"),
        allowed_web_domains=["example.com"],
        run_queue_name="jaxon:test:runs",
    )


@pytest.fixture
def registry() -> SkillRegistry:
    return build_default_registry()


@pytest.fixture
def ctx(settings: Settings) -> SkillContext:
    return SkillContext(settings=settings)
