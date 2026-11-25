"""Bootstrap the owner tenant, owner user and the owner's businesses. Idempotent.

Usage (after `alembic upgrade head`):
    python -m jackson_api.seed --email OWNER_EMAIL --auth-subject AUTH_PROVIDER_USER_ID

`--auth-subject` is the user id the hosted auth provider puts in the JWT `sub`
claim (for Clerk it looks like `user_...`). Running twice changes nothing.
"""

import argparse
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import bind_tenant, make_sessionmaker
from .models import AuditEvent, Business, Membership, Tenant, User
from .settings import load_settings

OWNER_TENANT_NAME = "Steady (owner)"

# source_system="seed" + source_id makes re-runs idempotent via the unique constraint.
OWNER_BUSINESSES = [
    ("steady-trades-university", "Steady Trades University", "education",
     "Trading education; checkout via Whop."),
    ("csw-trades", "CSW Trades", "education", None),
    ("steady-ventures", "Steady Ventures", "agency",
     "AI automation agency. Client work runs in separate client tenants (ADR-010)."),
    ("apex-7", "APEX-7", "trading_research",
     "Simulated futures bot. Research and paper trading only; no live trades (ADR-011)."),
    ("shopify-store", "Shopify Store", "ecommerce", None),
]


def seed_owner(session: Session, email: str, auth_subject: str) -> uuid.UUID:
    tenant = session.scalar(select(Tenant).where(Tenant.name == OWNER_TENANT_NAME))
    if tenant is None:
        tenant = Tenant(name=OWNER_TENANT_NAME)
        session.add(tenant)
        session.flush()

    user = session.scalar(select(User).where(User.auth_subject == auth_subject))
    if user is None:
        user = User(auth_subject=auth_subject, email=email, display_name="Steady")
        session.add(user)
        session.flush()

    if session.scalar(
        select(Membership).where(Membership.tenant_id == tenant.id, Membership.user_id == user.id)
    ) is None:
        session.add(Membership(tenant_id=tenant.id, user_id=user.id, role="owner"))

    bind_tenant(session, tenant.id)
    existing = set(
        session.scalars(
            select(Business.source_id).where(
                Business.tenant_id == tenant.id, Business.source_system == "seed"
            )
        )
    )
    for source_id, name, kind, description in OWNER_BUSINESSES:
        if source_id in existing:
            continue
        b = Business(
            tenant_id=tenant.id, name=name, kind=kind, description=description,
            source_system="seed", source_id=source_id,
        )
        session.add(b)
        session.flush()
        session.add(AuditEvent(
            tenant_id=tenant.id, actor_user_id=user.id, action="business.seeded",
            entity_type="Business", entity_id=b.id, data={"name": name},
        ))
    session.commit()
    return tenant.id


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--email", required=True)
    p.add_argument("--auth-subject", required=True)
    args = p.parse_args()
    url = load_settings().database_url
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    with make_sessionmaker(url)() as session:
        tenant_id = seed_owner(session, args.email, args.auth_subject)
    print(f"owner tenant ready: {tenant_id}")


if __name__ == "__main__":
    main()
