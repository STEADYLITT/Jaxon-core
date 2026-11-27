"""Bootstrap the owner tenant, owner user and the owner's businesses. Idempotent.

Usage (after `alembic upgrade head`):
    python -m jackson_api.seed --email OWNER_EMAIL --auth-subject AUTH_PROVIDER_USER_ID

`--auth-subject` is the user id the hosted auth provider puts in the JWT `sub`
claim (for Clerk it looks like `user_...`). Running twice changes nothing.
"""

import argparse
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import bind_tenant, make_sessionmaker
from .models import AuditEvent, Business, Membership, Tenant, User
from .settings import load_settings

OWNER_TENANT_NAME = "Steady (owner)"

# source_system="seed" + source_id makes re-runs idempotent via the unique constraint.
OWNER_BUSINESSES = [  # (source_id, name, kind, description, business line — ADR-039)
    ("steady-trades-university", "Steady Trades University", "education",
     "Trading education: Replay Trainer ($19.99/mo), One-on-One, Self-Paced, Live Group, "
     "indicators. Checkout via Whop.", "courses"),
    ("csw-trades", "CSW Trades", "education", None, "courses"),
    ("steady-ventures", "Steady Ventures", "agency",
     "Marketing agency for other companies. Client work runs in separate client tenants "
     "(ADR-010).", "agency"),
    ("apex-7", "APEX-7", "trading_research",
     "Simulated futures bot. Research and paper trading only; no live trades (ADR-011).",
     "trading"),
    ("shopify-store", "Shopify Store", "ecommerce", None, "ecommerce"),
    ("clip-farming", "Clip Farming", "clip_farming",
     "Clips from creators who allow clipping, posted to TikTok/Shorts/Reels.", "clips"),
    ("creator-agency", "Creator Agency", "creator_agency",
     "Manages creators who signed with Steady. SFW promo only; drafts wait for approval.",
     "creators"),
    ("ai-influencers", "AI Influencers", "ai_influencer",
     "SFW brand/lifestyle AI accounts, labeled as AI per platform rules.", "influencers"),
    # Steady's other companies named in the full-scope brief (descriptions left for Steady).
    ("amazon-university", "Amazon University (with Jay)", "education", None, "courses"),
    ("ai-wealth-academy", "AI Wealth Academy", "education", None, "courses"),
    ("steady-ai", "Steady AI", "education", None, "courses"),
    ("make-it-blueprints", "Make It (blueprints)", "education", None, "courses"),
    ("atom-labs", "Atom Labs (with Q)", "brand", None, "marketing"),
    ("steady-growth-os", "Steady Growth OS", "brand", None, "marketing"),
    ("shred-90", "SHRED 90", "brand", None, "marketing"),
    ("gottink", "GOTTINK / InkFlow CRM", "software", None, "factory"),
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
    for source_id, name, kind, description, line in OWNER_BUSINESSES:
        if source_id in existing:
            b = session.scalar(select(Business).where(
                Business.tenant_id == tenant.id, Business.source_system == "seed",
                Business.source_id == source_id))
            if b is not None and b.line is None:  # place it in its line once; owner may move it
                b.line = line
            continue
        b = Business(
            tenant_id=tenant.id, name=name, kind=kind, description=description,
            source_system="seed", source_id=source_id, line=line,
        )
        session.add(b)
        session.flush()
        session.add(AuditEvent(
            tenant_id=tenant.id, actor_user_id=user.id, action="business.seeded",
            entity_type="Business", entity_id=b.id, data={"name": name},
        ))
    session.flush()
    propose_line_agents(session, tenant.id, user.id)
    session.commit()
    return tenant.id


def propose_line_agents(session: Session, tenant_id, user_id) -> int:
    """Missing roles in each business line become PROPOSED agents (AgentSpec drafts). They
    do nothing until Steady validates, evaluates and activates them (agents API)."""
    from .lines import LINES, MAX_PROPOSED_AGENTS
    from .models import AgentSpec

    made = 0
    total = session.scalar(select(func.count()).select_from(AgentSpec).where(
        AgentSpec.tenant_id == tenant_id, AgentSpec.created_by == "factory")) or 0
    for ln in LINES:
        biz = session.scalar(select(Business.id).where(Business.tenant_id == tenant_id,
                                                       Business.line == ln.key)
                             .order_by(Business.created_at).limit(1))
        for p in ln.proposed:
            if session.scalar(select(AgentSpec.id).where(AgentSpec.tenant_id == tenant_id,
                                                         AgentSpec.name == p["name"])):
                continue
            if total + made >= MAX_PROPOSED_AGENTS:  # vol. 05: total agents are capped
                return made
            spec = AgentSpec(tenant_id=tenant_id, business_id=biz, name=p["name"], version=1,
                             role=p["role"], objective=p["objective"],
                             instructions=p["instructions"], status="draft",
                             created_by="factory", owner_user_id=user_id,
                             source_ref=f"line:{ln.key}")
            session.add(spec)
            session.flush()
            session.add(AuditEvent(tenant_id=tenant_id, actor_user_id=None,
                                   action="agent.proposed", entity_type="AgentSpec",
                                   entity_id=spec.id, data={"line": ln.key, "name": p["name"]}))
            made += 1
    return made


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
