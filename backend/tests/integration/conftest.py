"""Integration fixtures: REAL PostgreSQL+pgvector and Redis. Skipped if unreachable."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from jaxon.runtime import Runtime
from tests.fixtures import ScriptedProvider

BACKEND = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def _alembic(*args: str, url: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        check=True,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
    )


@pytest.fixture(scope="session")
def migrated_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://jaxon:jaxon@localhost:5432/jaxon")
    try:
        _alembic("upgrade", "head", url=url)
    except subprocess.CalledProcessError as e:
        pytest.skip(f"PostgreSQL not available / migration failed: {e.stderr.decode()[-300:]}")
    return url


@pytest.fixture
async def runtime(settings, migrated_db_url):
    settings.database_url = migrated_db_url
    provider = ScriptedProvider([])
    rt = Runtime.build(settings, provider=provider)
    try:
        await rt.redis.ping()
    except Exception as e:
        await rt.aclose()
        pytest.skip(f"Redis not available: {e}")
    async with rt.db.engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE run_steps, agent_runs, agent_definitions, memories "
                "RESTART IDENTITY CASCADE"
            )
        )
    await rt.redis.flushdb()
    yield rt
    await rt.redis.flushdb()
    await rt.aclose()
