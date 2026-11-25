"""Model usage metering (phase 08, ADR-018).

Revision ID: 0005_model_usage
Revises: 0004_policy
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_model_usage"
down_revision = "0004_policy"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "model_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("profile", sa.String(32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd_micros", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("mode IN ('live','mock')", name="ck_model_usage_mode"),
    )
    op.create_index("ix_model_usage_tenant_id", "model_usage", ["tenant_id"])
    op.create_index("ix_model_usage_run_id", "model_usage", ["run_id"])
    op.execute(f"GRANT SELECT ON model_usage TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON model_usage TO {WORKER}")
    op.execute("ALTER TABLE model_usage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE model_usage FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON model_usage "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON model_usage TO {WORKER} "
               f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.drop_table("model_usage")
