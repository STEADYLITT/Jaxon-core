import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from jackson_api.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = (
        config.attributes.get("url")
        or os.environ.get("MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise RuntimeError("Set MIGRATION_DATABASE_URL (schema owner) to run migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
