"""Clip payouts and creator earnings (ADR-057): one row per payout exactly as the source
reported it, recorded once.

Revision ID: 0027_payouts
Revises: 0026_ad_insights
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_payouts"
down_revision = "0026_ad_insights"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "payout_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("line", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(200), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("platform", sa.String(32)),
        sa.Column("campaign", sa.String(200)),
        sa.Column("views", sa.BigInteger()),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("line IN ('clips','creators')", name="ck_payout_line"),
        sa.CheckConstraint("source IN ('feed','import')", name="ck_payout_source"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_payout_amount"),
        sa.CheckConstraint("views IS NULL OR views >= 0", name="ck_payout_views"),
        sa.UniqueConstraint("tenant_id", "line", "reference", name="uq_payout_ref"),
    )
    op.create_index("ix_payout_lines_tenant_id", "payout_lines", ["tenant_id"])
    # Append-only, like revenue: the worker records, nobody edits or deletes.
    op.execute(f"GRANT SELECT ON payout_lines TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON payout_lines TO {WORKER}")
    op.execute("ALTER TABLE payout_lines ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payout_lines FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON payout_lines "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON payout_lines TO {WORKER} "
               f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.execute("DROP TABLE payout_lines")
