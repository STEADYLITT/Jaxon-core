"""Shopify draft products (phase 15, ADR-025).

Revision ID: 0012_shop
Revises: 0011_economics
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_shop"
down_revision = "0011_economics"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "shop_products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id")),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("opportunity_id", sa.Uuid(), sa.ForeignKey("opportunities.id")),
        sa.Column("backend", sa.String(16), nullable=False),
        sa.Column("store", sa.String(255)),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("handle", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("admin_url", sa.Text()),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("tenant_id", "backend", "handle"),
        sa.CheckConstraint("backend IN ('mock','shopify')", name="ck_shop_backend"),
        sa.CheckConstraint("status IN ('DRAFT','ACTIVE','ARCHIVED','UNLISTED')",
                           name="ck_shop_status"),
    )
    op.create_index("ix_shop_products_tenant_id", "shop_products", ["tenant_id"])
    op.execute(f"GRANT SELECT ON shop_products TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON shop_products TO {WORKER}")
    op.execute(f"GRANT UPDATE (status, verified, verified_at, updated_at) ON shop_products "
               f"TO {WORKER}")
    op.execute("ALTER TABLE shop_products ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shop_products FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON shop_products "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON shop_products TO {WORKER} "
               f"USING (true) WITH CHECK (true)")
    # New products can only ever be created as drafts.
    op.execute(f"CREATE POLICY worker_drafts_only ON shop_products AS RESTRICTIVE FOR INSERT "
               f"TO {WORKER} WITH CHECK (status = 'DRAFT')")


def downgrade() -> None:
    op.drop_table("shop_products")
