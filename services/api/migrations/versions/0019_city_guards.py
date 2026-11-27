"""City phase 6 (ADR-037): per-agent daily spend limit (cost guard) and read-only
share links for a client's view of the City.

Revision ID: 0019_city_guards
Revises: 0018_city
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_city_guards"
down_revision = "0018_city"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    # NULL = the default limit (CITY_AGENT_DAILY_LIMIT_USD). Only the owner changes it.
    op.add_column("agents", sa.Column("daily_limit_usd", sa.Numeric(10, 2)))
    op.execute(f"GRANT UPDATE (daily_limit_usd) ON agents TO {APP}")
    op.create_table(
        "city_shares",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("label", sa.String(120), nullable=False, server_default=""),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )
    op.create_index("ix_city_shares_tenant_id", "city_shares", ["tenant_id"])
    op.execute(f"GRANT SELECT, INSERT ON city_shares TO {APP}")
    op.execute(f"GRANT UPDATE (revoked_at) ON city_shares TO {APP}")
    op.execute("ALTER TABLE city_shares ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE city_shares FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON city_shares "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")


def downgrade() -> None:
    op.drop_table("city_shares")
    op.drop_column("agents", "daily_limit_usd")
