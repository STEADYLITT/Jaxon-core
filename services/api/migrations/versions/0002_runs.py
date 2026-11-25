"""Commands and runs: workflow_runs, tasks, run_events (+ RLS, least privilege).

Revision ID: 0002_runs
Revises: 0001_initial
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_runs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

APP_ROLE = "jackson_app"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
STATES = "status IN ('queued','running','waiting_approval','succeeded','failed','canceled')"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("command_type", sa.String(16), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("budget_cents", sa.BigInteger()),
        sa.Column("deadline", sa.DateTime(timezone=True)),
        sa.Column("constraints", postgresql.JSONB(), nullable=False),
        sa.Column("success_criteria", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.CheckConstraint(STATES, name="ck_workflow_run_status"),
        sa.CheckConstraint("budget_cents IS NULL OR budget_cents >= 0", name="ck_run_budget"),
    )
    op.create_index("ix_workflow_runs_tenant_id", "workflow_runs", ["tenant_id"])
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("run_id", "sequence"),
        sa.CheckConstraint(STATES, name="ck_task_status"),
    )
    op.create_index("ix_tasks_tenant_id", "tasks", ["tenant_id"])
    op.create_index("ix_tasks_run_id", "tasks", ["run_id"])
    op.create_table(
        "run_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        _ts("created_at"),
    )
    op.create_index("ix_run_events_tenant_id", "run_events", ["tenant_id"])
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON workflow_runs, tasks TO {APP_ROLE}")
    # Run history is append-only for the app, like the audit log.
    op.execute(f"GRANT SELECT, INSERT ON run_events TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SEQUENCE run_events_id_seq TO {APP_ROLE}")

    for table in ("workflow_runs", "tasks", "run_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )


def downgrade() -> None:
    for table in ("run_events", "tasks", "workflow_runs"):
        op.drop_table(table)
