"""Opportunities (phase 13, ADR-023).

Revision ID: 0010_opportunities
Revises: 0009_agents
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_opportunities"
down_revision = "0009_agents"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("dedupe_key", sa.String(300), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("summary", sa.Text()),
        sa.Column("signals", postgresql.JSONB(), nullable=False),
        sa.Column("scores", postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("uncertainty", sa.Float(), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("sources", postgresql.JSONB(), nullable=False),
        sa.Column("economics", postgresql.JSONB()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(300)),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("tenant_id", "dedupe_key"),
        sa.CheckConstraint("status IN ('new','researching','validated','rejected','pursuing')",
                           name="ck_opportunity_status"),
    )
    op.create_index("ix_opportunities_tenant_id", "opportunities", ["tenant_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON opportunities TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON opportunities TO {WORKER}")
    # Research may refresh evidence and scores, never the owner's decision (status).
    op.execute(f"GRANT UPDATE (summary, category, signals, scores, score, uncertainty, brief, "
               f"sources, updated_at) ON opportunities TO {WORKER}")
    op.execute("ALTER TABLE opportunities ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE opportunities FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON opportunities "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON opportunities TO {WORKER} "
               f"USING (true) WITH CHECK (true)")
    op.execute(f"CREATE POLICY worker_new_only ON opportunities AS RESTRICTIVE FOR INSERT "
               f"TO {WORKER} WITH CHECK (status = 'new')")


def downgrade() -> None:
    op.drop_table("opportunities")
