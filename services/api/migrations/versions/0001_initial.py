"""Initial schema: tenants, users, memberships, businesses, audit_events + row-level security.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-25

Security model (ADR-013):
- Migrations run as the schema owner (MIGRATION_DATABASE_URL).
- The API connects as role `jackson_app` (DATABASE_URL), which is NOT the table
  owner and NOT a superuser, so Postgres row-level security always applies to it.
- Tenant-scoped tables only show rows where tenant_id = current_setting('app.tenant_id').
  If the setting is absent, no rows are visible (deny by default).
- audit_events is append-only for the app role (SELECT, INSERT only).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = "jackson_app"
RLS_TABLES = ("businesses", "audit_events")
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("auth_subject", sa.String(255), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200)),
        sa.Column("status", sa.String(32), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("tenant_id", "user_id"),
        sa.CheckConstraint(
            "role IN ('owner','admin','member','viewer')", name="ck_membership_role"
        ),
    )
    op.create_table(
        "businesses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_system", sa.String(64)),
        sa.Column("source_id", sa.String(255)),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("tenant_id", "source_system", "source_id"),
    )
    op.create_index("ix_businesses_tenant_id", "businesses", ["tenant_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.Uuid()),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _ts("created_at"),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])

    # --- App role + least-privilege grants ---------------------------------
    op.execute(
        f"DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} NOLOGIN; END IF; END $$"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, users, memberships, businesses "
        f"TO {APP_ROLE}"
    )
    op.execute(f"GRANT SELECT, INSERT ON audit_events TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON alembic_version TO {APP_ROLE}")

    # --- Row-level security ----------------------------------------------------
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )


def downgrade() -> None:
    for table in ("audit_events", "businesses", "memberships", "users", "tenants"):
        op.drop_table(table)
    op.execute(f"REVOKE ALL ON alembic_version FROM {APP_ROLE}")
