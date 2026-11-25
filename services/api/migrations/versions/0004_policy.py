"""Policy: business autonomy levels, approvals, append-only budget ledger.

Revision ID: 0004_policy
Revises: 0003_worker
Create Date: 2026-11-25

DB-enforced guarantees (ADR-017):
- The worker (agents) can INSERT approvals but never UPDATE them: no agent can approve
  its own restricted action.
- The worker can't change a business's autonomy level (SELECT only on businesses).
- Ledger entries are append-only for every app role.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_policy"
down_revision = "0003_worker"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.add_column("businesses", sa.Column("autonomy_level", sa.Integer(), nullable=False,
                                          server_default="1"))
    op.alter_column("businesses", "autonomy_level", server_default=None)
    op.create_check_constraint("ck_business_autonomy", "businesses",
                               "autonomy_level BETWEEN 0 AND 3")

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_note", sa.Text()),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("task_id", "fingerprint"),
        sa.CheckConstraint("status IN ('pending','approved','rejected')",
                           name="ck_approval_status"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_approval_amount"),
    )
    op.create_index("ix_approvals_tenant_id", "approvals", ["tenant_id"])
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("reservation_id", sa.Uuid(), sa.ForeignKey("ledger_entries.id")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("tenant_id", "idempotency_key"),
        sa.CheckConstraint("kind IN ('reserve','commit','release')", name="ck_ledger_kind"),
        sa.CheckConstraint("amount_cents >= 0", name="ck_ledger_amount"),
    )
    op.create_index("ix_ledger_entries_tenant_id", "ledger_entries", ["tenant_id"])
    op.create_index("ix_ledger_entries_run_id", "ledger_entries", ["run_id"])

    op.execute(f"GRANT SELECT, UPDATE ON approvals TO {APP}")
    op.execute(f"GRANT SELECT ON ledger_entries TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON approvals, ledger_entries TO {WORKER}")
    for table in ("approvals", "ledger_entries"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
        op.execute(f"CREATE POLICY worker_access ON {table} TO {WORKER} "
                   f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.drop_table("ledger_entries")
    op.drop_table("approvals")
    op.drop_constraint("ck_business_autonomy", "businesses")
    op.drop_column("businesses", "autonomy_level")
