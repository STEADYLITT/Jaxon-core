"""FastAPI dependencies: DB session, authenticated user, tenant membership.

Deny by default:
- auth not configured        -> 503 (never silently open)
- missing/invalid token      -> 401
- valid token, unknown user  -> 403 (no auto-provisioning; owner seeds users)
- not a member of tenant     -> 403
- row in another tenant      -> 404 (queries + Postgres RLS both scope by tenant)
"""

import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import AuthConfig, AuthError, TokenVerifier
from .db import bind_tenant, make_sessionmaker
from .models import WRITE_ROLES, Membership, User
from .settings import load_settings


@lru_cache(maxsize=1)
def _default_verifier() -> TokenVerifier | None:
    s = load_settings()
    if not s.auth_configured:
        return None
    return TokenVerifier(
        AuthConfig(
            issuer=s.auth_issuer,
            jwks_url=s.auth_jwks_url,
            audience=s.auth_audience,
            authorized_parties=s.auth_authorized_parties,
        )
    )


def get_verifier() -> TokenVerifier:
    v = _default_verifier()
    if v is None:
        raise HTTPException(503, "authentication is not configured (AUTH_ISSUER/AUTH_JWKS_URL)")
    return v


def get_db() -> Iterator[Session]:
    url = load_settings().database_url
    if not url:
        raise HTTPException(503, "database is not configured (DATABASE_URL)")
    session = make_sessionmaker(url)()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DB = Annotated[Session, Depends(get_db)]


def current_user(
    db: DB,
    verifier: Annotated[TokenVerifier, Depends(get_verifier)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = verifier.verify(authorization.split(" ", 1)[1].strip())
    except AuthError:
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"}) from None
    user = db.scalar(select(User).where(User.auth_subject == claims["sub"]))
    if user is None or user.status != "active":
        raise HTTPException(403, "user is not registered in Jackson OS")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def tenant_membership(
    tenant_id: Annotated[uuid.UUID, Path()], user: CurrentUser, db: DB
) -> Membership:
    m = db.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant_id,
            Membership.user_id == user.id,
            Membership.status == "active",
        )
    )
    if m is None:
        raise HTTPException(403, "not a member of this tenant")
    bind_tenant(db, tenant_id)  # activates row-level security for this transaction
    return m


TenantMember = Annotated[Membership, Depends(tenant_membership)]


def require_write(m: TenantMember) -> Membership:
    if m.role not in WRITE_ROLES:
        raise HTTPException(403, f"role '{m.role}' cannot modify this tenant")
    return m


TenantWriter = Annotated[Membership, Depends(require_write)]
