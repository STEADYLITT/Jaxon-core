"""Personas and content items (phase 16, ADR-026).

Revision ID: 0013_content
Revises: 0012_shop
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_content"
down_revision = "0012_shop"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
STATUSES = "('draft', 'in_review', 'approved', 'rejected', 'scheduled', 'published', 'failed')"
CHANNELS = "('instagram', 'tiktok', 'youtube_shorts', 'x', 'facebook', 'email', 'blog')"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "personas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("fictional", sa.Boolean(), nullable=False),
        sa.Column("bio", sa.Text(), nullable=False),
        sa.Column("brand_bible", postgresql.JSONB(), nullable=False),
        sa.Column("disclosure", sa.String(300), nullable=False),
        sa.Column("do_not_impersonate", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint("kind IN ('virtual_influencer','brand_voice')", name="ck_persona_kind"),
        sa.CheckConstraint("kind <> 'virtual_influencer' OR fictional",
                           name="ck_persona_fictional"),
        sa.CheckConstraint("length(disclosure) > 0", name="ck_persona_disclosure"),
    )
    op.create_index("ix_personas_tenant_id", "personas", ["tenant_id"])
    op.create_table(
        "content_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("persona_id", sa.Uuid(), sa.ForeignKey("personas.id")),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id")),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("hashtags", postgresql.JSONB(), nullable=False),
        sa.Column("sponsored", sa.Boolean(), nullable=False),
        sa.Column("assets", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("review", postgresql.JSONB(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("external_post_id", sa.String(255)),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_content_status"),
        sa.CheckConstraint(f"channel IN {CHANNELS}", name="ck_content_channel"),
        sa.CheckConstraint("status NOT IN ('approved','scheduled','published') "
                           "OR approved_by IS NOT NULL", name="ck_content_needs_approval"),
    )
    op.create_index("ix_content_items_tenant_id", "content_items", ["tenant_id"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON personas, content_items TO {APP}")
    # An owner's "schedule" click is recorded as a pre-approved posting approval.
    op.execute(f"GRANT INSERT ON approvals TO {APP}")
    op.execute(f"GRANT SELECT ON personas TO {WORKER}")
    op.execute(f"GRANT SELECT, INSERT ON content_items TO {WORKER}")
    # Worker may move status and record review/post ids, but never approved_by/approved_at:
    # combined with the CHECK constraint, agents can't approve their own content.
    op.execute(f"GRANT UPDATE (status, review, external_post_id, updated_at) "
               f"ON content_items TO {WORKER}")
    for table in ("personas", "content_items"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
        op.execute(f"CREATE POLICY worker_access ON {table} TO {WORKER} "
                   f"USING (true) WITH CHECK (true)")
    op.execute(f"CREATE POLICY worker_new_drafts_only ON content_items AS RESTRICTIVE "
               f"FOR INSERT TO {WORKER} WITH CHECK (status IN ('draft','in_review'))")


def downgrade() -> None:
    op.drop_table("content_items")
    op.drop_table("personas")
