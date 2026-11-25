"""Artifacts and visual browser sessions (phase 11, ADR-021).

Revision ID: 0008_browser
Revises: 0007_tools
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_browser"
down_revision = "0007_tools"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id")),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=False),
        _ts("created_at"),
    )
    op.create_index("ix_artifacts_tenant_id", "artifacts", ["tenant_id"])
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("control", sa.String(16), nullable=False),
        sa.Column("allowed_domains", postgresql.JSONB(), nullable=False),
        sa.Column("current_url", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("last_action", sa.Text()),
        sa.Column("last_screenshot_id", sa.Uuid(), sa.ForeignKey("artifacts.id")),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("status IN ('active','paused','taken_over','terminated','closed')",
                           name="ck_browser_status"),
        sa.CheckConstraint("control IN ('none','pause','takeover','terminate')",
                           name="ck_browser_control"),
    )
    op.create_index("ix_browser_sessions_tenant_id", "browser_sessions", ["tenant_id"])

    op.execute(f"GRANT SELECT ON artifacts, browser_sessions TO {APP}")
    op.execute(f"GRANT UPDATE (control, updated_at) ON browser_sessions TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON artifacts, browser_sessions TO {WORKER}")
    # Column-level: the worker reports state but can never write the owner's `control`.
    op.execute(f"GRANT UPDATE (status, current_url, title, last_action, last_screenshot_id, "
               f"ended_at, updated_at) ON browser_sessions TO {WORKER}")
    for table in ("artifacts", "browser_sessions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
        op.execute(f"CREATE POLICY worker_access ON {table} TO {WORKER} "
                   f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.drop_table("browser_sessions")
    op.drop_table("artifacts")
