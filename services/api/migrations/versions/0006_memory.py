"""Memory with provenance (phase 09, ADR-019).

Revision ID: 0006_memory
Revises: 0005_model_usage
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_memory"
down_revision = "0005_model_usage"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "memory_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("epistemic", sa.String(16), nullable=False),
        sa.Column("key", sa.String(200)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("impact", sa.String(8), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(500)),
        sa.Column("source_detail", postgresql.JSONB(), nullable=False),
        sa.Column("access_scope", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(),
                  sa.ForeignKey("memory_records.id", ondelete="SET NULL")),
        sa.Column("created_by_user", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("created_by_agent", sa.String(200)),
        sa.Column("retain_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("kind IN ('preference', 'business_fact', 'decision', "
                           "'project_state', 'tool_result', 'lesson')", name="ck_memory_kind"),
        sa.CheckConstraint("epistemic IN ('fact', 'inference', 'hypothesis')",
                           name="ck_memory_epistemic"),
        sa.CheckConstraint("access_scope IN ('tenant', 'owner_only')", name="ck_memory_scope"),
        sa.CheckConstraint("status IN ('active', 'pending_confirmation', 'superseded', "
                           "'rejected')", name="ck_memory_status"),
        sa.CheckConstraint("impact IN ('low','high')", name="ck_memory_impact"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memory_confidence"),
    )
    op.create_index("ix_memory_records_tenant_id", "memory_records", ["tenant_id"])
    op.create_index("ix_memory_key", "memory_records", ["tenant_id", "key"])
    op.execute("CREATE INDEX ix_memory_fts ON memory_records "
               "USING gin (to_tsvector('english', content))")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON memory_records TO {APP}")
    # Agents may remember and recall, never rewrite or erase.
    op.execute(f"GRANT SELECT, INSERT ON memory_records TO {WORKER}")
    op.execute("ALTER TABLE memory_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_records FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON memory_records "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON memory_records TO {WORKER} "
               f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.drop_table("memory_records")
