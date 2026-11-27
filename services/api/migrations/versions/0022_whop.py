"""Whop connector (ADR-041): product/plan -> business links (and course checkout links),
memberships as Whop reports them, and Whop sales recorded once each.

Revision ID: 0022_whop
Revises: 0021_courses
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_whop"
down_revision = "0021_courses"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON {table} "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON {table} TO {WORKER} "
               f"USING (true) WITH CHECK (true)")


def upgrade() -> None:
    op.create_table(
        "whop_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("course_id", sa.Uuid(), sa.ForeignKey("courses.id")),
        sa.Column("product_id", sa.String(64)),
        sa.Column("variant_id", sa.String(64)),
        sa.Column("checkout_id", sa.String(64)),
        sa.Column("purchase_url", sa.String(500)),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("product_id IS NOT NULL OR variant_id IS NOT NULL",
                           name="ck_whop_link_target"),
    )
    op.create_index("ix_whop_links_tenant_id", "whop_links", ["tenant_id"])
    op.execute(f"GRANT SELECT, INSERT ON whop_links TO {APP}")
    op.execute(f"GRANT SELECT ON whop_links TO {WORKER}")
    _rls("whop_links")

    op.create_table(
        "whop_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("membership_id", sa.String(64), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("product_id", sa.String(64)),
        sa.Column("variant_id", sa.String(64)),
        sa.Column("user_id", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("discord_id", sa.String(64)),
        sa.Column("telegram_id", sa.String(64)),
        sa.Column("period_end", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("tenant_id", "membership_id", name="uq_whop_member"),
    )
    op.create_index("ix_whop_members_tenant_id", "whop_members", ["tenant_id"])
    # The webhook (app role) and the sync task (worker role) both keep this current.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON whop_members TO {APP}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON whop_members TO {WORKER}")
    _rls("whop_members")

    # A Whop sale is recorded once, whether the webhook or the sync sees it first.
    op.execute("CREATE UNIQUE INDEX uq_revenue_whop_ref ON revenue_entries (tenant_id, reference) "
               "WHERE source = 'whop'")
    op.execute(f"GRANT INSERT ON revenue_entries TO {WORKER}")


def downgrade() -> None:
    op.execute(f"REVOKE INSERT ON revenue_entries FROM {WORKER}")
    op.execute("DROP INDEX uq_revenue_whop_ref")
    op.execute("DROP TABLE whop_members")
    op.execute("DROP TABLE whop_links")
