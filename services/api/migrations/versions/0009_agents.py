"""Agent registry and delegation (phase 12, ADR-022).

Revision ID: 0009_agents
Revises: 0008_browser
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_agents"
down_revision = "0008_browser"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "agent_specs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(200), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("model_profile", sa.String(32), nullable=False),
        sa.Column("allowed_tools", postgresql.JSONB(), nullable=False),
        sa.Column("budget_cents_per_run", sa.Integer(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("max_runtime_s", sa.Integer(), nullable=False),
        sa.Column("success_tests", postgresql.JSONB(), nullable=False),
        sa.Column("output_checks", postgresql.JSONB(), nullable=False),
        sa.Column("escalation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(300)),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("previous_version_id", sa.Uuid(), sa.ForeignKey("agent_specs.id")),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("tenant_id", "name", "version"),
        sa.CheckConstraint("status IN ('draft','active','retired')", name="ck_agent_status"),
        sa.CheckConstraint("budget_cents_per_run >= 0", name="ck_agent_budget"),
    )
    op.create_index("ix_agent_specs_tenant_id", "agent_specs", ["tenant_id"])
    op.add_column("tasks", sa.Column("parent_task_id", sa.Uuid(), sa.ForeignKey("tasks.id")))
    op.add_column("tasks", sa.Column("depth", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("tasks", "depth", server_default=None)
    op.add_column("tasks", sa.Column("agent_spec_id", sa.Uuid(),
                                     sa.ForeignKey("agent_specs.id")))
    op.add_column("tasks", sa.Column("tool_scope", postgresql.JSONB()))
    op.add_column("tasks", sa.Column("input", postgresql.JSONB()))

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON agent_specs TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON agent_specs TO {WORKER}")
    op.execute("ALTER TABLE agent_specs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_specs FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON agent_specs "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON agent_specs TO {WORKER} "
               f"USING (true) WITH CHECK (true)")
    # RESTRICTIVE: ANDed with every other policy, so no path lets the worker (the agent
    # factory) insert anything but a draft. Activation is an owner action in the API.
    op.execute(f"CREATE POLICY worker_drafts_only ON agent_specs AS RESTRICTIVE FOR INSERT "
               f"TO {WORKER} WITH CHECK (status = 'draft')")


def downgrade() -> None:
    for col in ("input", "tool_scope", "agent_spec_id", "depth", "parent_task_id"):
        op.drop_column("tasks", col)
    op.drop_table("agent_specs")
