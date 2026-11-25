"""Database engine and tenant-scoped sessions."""

import uuid
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


@lru_cache(maxsize=4)
def get_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


def bind_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    """Scope the current transaction to one tenant for Postgres row-level security.

    `is_local = true` means the setting dies with the transaction, so a pooled
    connection can never leak one tenant's scope into another request.
    """
    session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )
