"""Owner dashboard: global stop + revenue entries (phase 19, ADR-029).

Revision ID: 0016_dashboard
Revises: 0015_crm
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_dashboard"
down_revision = "0015_crm"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column("tenants", sa.Column("stopped_at", sa.DateTime(timezone=True)))
    op.add_column("tenants", sa.Column("stopped_by", sa.Uuid()))
    op.add_column("tenants", sa.Column("stop_reason", sa.Text()))

    op.create_table(
        "revenue_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("reference", sa.String(300), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("kind IN ('estimate','actual','refund')", name="ck_revenue_kind"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_revenue_amount"),
        sa.CheckConstraint("source IN ('manual','shopify','whop','stripe','import')",
                           name="ck_revenue_source"),
        sa.CheckConstraint("period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_revenue_period"),
    )
    op.create_index("ix_revenue_entries_tenant_id", "revenue_entries", ["tenant_id"])
    op.execute(f"GRANT SELECT, INSERT ON revenue_entries TO {APP}")  # append-only
    op.execute(f"GRANT SELECT ON revenue_entries TO {WORKER}")
    op.execute("ALTER TABLE revenue_entries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE revenue_entries FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON revenue_entries "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON revenue_entries TO {WORKER} "
               f"USING (true) WITH CHECK (true)")

    # Global stop, enforced in the database: while a tenant is stopped, no run can be
    # created and no task can start, whoever tries (API, worker, a stray script).
    op.execute("""
        CREATE FUNCTION tenant_not_stopped() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_TABLE_NAME = 'tasks' AND NOT (NEW.status = 'running'
                AND (TG_OP = 'INSERT' OR OLD.status <> 'running')) THEN
            RETURN NEW;
          END IF;
          IF EXISTS (SELECT 1 FROM tenants WHERE id = NEW.tenant_id
                     AND stopped_at IS NOT NULL) THEN
            RAISE EXCEPTION 'tenant_stopped: global stop is on for this tenant';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("CREATE TRIGGER tenant_not_stopped BEFORE INSERT ON workflow_runs "
               "FOR EACH ROW EXECUTE FUNCTION tenant_not_stopped()")
    op.execute("CREATE TRIGGER tenant_not_stopped BEFORE INSERT OR UPDATE OF status ON tasks "
               "FOR EACH ROW EXECUTE FUNCTION tenant_not_stopped()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tenant_not_stopped ON tasks")
    op.execute("DROP TRIGGER IF EXISTS tenant_not_stopped ON workflow_runs")
    op.execute("DROP FUNCTION IF EXISTS tenant_not_stopped()")
    op.drop_table("revenue_entries")
    for c in ("stop_reason", "stopped_by", "stopped_at"):
        op.drop_column("tenants", c)
