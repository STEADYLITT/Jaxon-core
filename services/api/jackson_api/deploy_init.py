"""First-boot and every-boot setup for a single-owner deployment (ADR-031). Idempotent.

1. Migrate the schema to the latest version (as the schema owner).
2. Give the least-privilege roles `jackson_app` and `jackson_worker` their login passwords
   (generated once, kept on the volume; see deploy_env).
3. If OWNER_EMAIL and OWNER_AUTH_SUBJECT are set, create the owner's tenant, user,
   membership and businesses (same as `python -m jackson_api.seed`).

Prints what it did, never a password.
"""

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine

from .deploy_env import derive, load_or_create_secrets

API_DIR = Path(__file__).resolve().parent.parent


def set_role_passwords(owner_url: str, passwords: dict[str, str]) -> None:
    eng = create_engine(owner_url, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            raw = c.connection.driver_connection  # psycopg connection: safe SQL composition
            with raw.cursor() as cur:
                for role, pw in passwords.items():
                    cur.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(pw)))
    finally:
        eng.dispose()


def ensure_connector_grants(session, tenant_id: str, owner_subject: str) -> list[str]:
    """When the owner has configured Shopify on the host, give the owner's workspace the
    `shopify.drafts` grant once (drafts only; publishing still needs an approval or a
    Launch approval). Recorded in the audit log as the owner's configuration."""
    import uuid as _uuid

    from sqlalchemy import select

    from .connectors.shopify import live_config
    from .db import bind_tenant
    from .models import AuditEvent, ToolGrant, User

    added = []
    tid = _uuid.UUID(tenant_id)
    bind_tenant(session, tid)
    owner = session.scalar(select(User).where(User.auth_subject == owner_subject))
    if live_config() and session.scalar(select(ToolGrant.id).where(
            ToolGrant.tenant_id == tid, ToolGrant.tool_name == "shopify.drafts",
            ToolGrant.status == "active")) is None:
        g = ToolGrant(tenant_id=tid, tool_name="shopify.drafts", status="active", config={},
                      granted_by=owner.id)
        session.add(g)
        session.flush()
        session.add(AuditEvent(tenant_id=tid, actor_user_id=owner.id, action="tool.granted",
                               entity_type="ToolGrant", entity_id=g.id,
                               data={"tool": "shopify.drafts",
                                     "via": "host configuration (SHOPIFY_* variables)"}))
        added.append("shopify.drafts")
    session.commit()
    return added


def run(env=os.environ) -> dict:
    owner = env.get("MIGRATION_DATABASE_URL")
    if not owner:
        raise SystemExit("MIGRATION_DATABASE_URL is not set (the Postgres owner connection)")
    secrets_ = load_or_create_secrets(env)
    owner_url = derive({"MIGRATION_DATABASE_URL": owner}).get("MIGRATION_DATABASE_URL", owner)

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.attributes["url"] = owner_url
    command.upgrade(cfg, "head")
    print("schema: up to date")

    set_role_passwords(owner_url, {"jackson_app": secrets_["APP_DB_PASSWORD"],
                                   "jackson_worker": secrets_["WORKER_DB_PASSWORD"]})
    print("database roles: jackson_app and jackson_worker can log in")

    done = {"migrated": True, "roles": True, "owner": None}
    email, subject = env.get("OWNER_EMAIL"), env.get("OWNER_AUTH_SUBJECT")
    if email and subject:
        from .db import make_sessionmaker
        from .seed import seed_owner

        app_url = env.get("DATABASE_URL") or derive(
            {"MIGRATION_DATABASE_URL": owner}, secrets_)["DATABASE_URL"]
        with make_sessionmaker(app_url)() as s:
            done["owner"] = str(seed_owner(s, email, subject))
            done["grants"] = ensure_connector_grants(s, done["owner"], subject)
        print(f"owner: ready (tenant {done['owner']})")
        for g in done["grants"]:
            print(f"connector: {g} enabled for the owner's workspace (drafts only)")
    else:
        print("owner: not set up yet (set OWNER_EMAIL and OWNER_AUTH_SUBJECT, see DEPLOY.md)")
    return done


if __name__ == "__main__":
    run()
    sys.exit(0)
