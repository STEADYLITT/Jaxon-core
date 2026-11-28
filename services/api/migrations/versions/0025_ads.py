"""Paid ads (ADR-048): campaigns Jackson drafted on Meta/TikTok, paused until an active
Launch switches them on.

Revision ID: 0025_ads
Revises: 0024_community
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_ads"
down_revision = "0024_community"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "ad_campaigns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("launch_id", sa.Uuid(), sa.ForeignKey("launches.id")),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id")),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("objective", sa.String(40), nullable=False),
        sa.Column("daily_budget_cents", sa.BigInteger(), nullable=False),
        sa.Column("countries", postgresql.JSONB(), nullable=False),
        sa.Column("copy", sa.Text(), nullable=False),
        sa.Column("external_campaign_id", sa.String(64), nullable=False),
        sa.Column("external_adset_id", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("platform IN ('meta','tiktok')", name="ck_ad_platform"),
        sa.CheckConstraint("status IN ('mock','draft','active','paused')", name="ck_ad_status"),
        sa.CheckConstraint("daily_budget_cents >= 100", name="ck_ad_budget"),
    )
    op.create_index("ix_ad_campaigns_tenant_id", "ad_campaigns", ["tenant_id"])
    op.execute(f"GRANT SELECT ON ad_campaigns TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON ad_campaigns TO {WORKER}")
    op.execute(f"GRANT UPDATE (status, launch_id, updated_at) ON ad_campaigns TO {WORKER}")
    op.execute("ALTER TABLE ad_campaigns ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ad_campaigns FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON ad_campaigns "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON ad_campaigns TO {WORKER} "
               f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.execute("DROP TABLE ad_campaigns")
