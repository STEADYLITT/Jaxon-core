import pytest
from sqlalchemy import create_engine, inspect

from tests.integration.conftest import _alembic

pytestmark = pytest.mark.integration


def test_downgrade_and_upgrade_roundtrip(migrated_db_url):
    _alembic("downgrade", "base", url=migrated_db_url)
    sync_url = migrated_db_url.replace("+asyncpg", "+psycopg")
    eng = create_engine(sync_url)
    assert "agent_runs" not in inspect(eng).get_table_names()
    _alembic("upgrade", "head", url=migrated_db_url)
    tables = set(inspect(eng).get_table_names())
    eng.dispose()
    assert {"agent_definitions", "agent_runs", "run_steps", "memories", "alembic_version"} <= tables


def test_models_match_migrations(migrated_db_url):
    _alembic("check", url=migrated_db_url)  # raises if autogenerate finds a diff
