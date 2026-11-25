"""Tool grants and the tool call log (phase 10, ADR-020).

Revision ID: 0007_tools
Revises: 0006_memory
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_tools"
down_revision = "0006_memory"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "tool_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("granted_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("status IN ('active','revoked')", name="ck_tool_grant_status"),
    )
    op.create_index("ix_tool_grants_lookup", "tool_grants", ["tenant_id", "tool_name", "status"])
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("input", postgresql.JSONB(), nullable=False),
        sa.Column("output", postgresql.JSONB()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.CheckConstraint("status IN ('succeeded','failed')", name="ck_tool_call_status"),
    )
    op.create_index("ix_tool_calls_tenant_id", "tool_calls", ["tenant_id"])
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON tool_grants TO {APP}")
    op.execute(f"GRANT SELECT ON tool_calls TO {APP}")
    op.execute(f"GRANT SELECT ON tool_grants TO {WORKER}")  # agents can't grant themselves
    op.execute(f"GRANT SELECT, INSERT ON tool_calls TO {WORKER}")
    for table in ("tool_grants", "tool_calls"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
        op.execute(f"CREATE POLICY worker_access ON {table} TO {WORKER} "
                   f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.drop_table("tool_calls")
    op.drop_table("tool_grants")
