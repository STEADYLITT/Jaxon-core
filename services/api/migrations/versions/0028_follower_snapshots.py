"""Platform follower analytics (ADR-058): one reading per account per day, as reported.

Revision ID: 0028_follower_snapshots
Revises: 0027_payouts
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_follower_snapshots"
down_revision = "0027_payouts"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "follower_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("handle", sa.String(120), nullable=False, server_default=""),
        sa.Column("followers", sa.BigInteger(), nullable=False),
        sa.Column("taken_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("followers >= 0", name="ck_follower_count"),
        sa.UniqueConstraint("tenant_id", "platform", "account_id", "taken_on",
                            name="uq_follower_day"),
    )
    op.create_index("ix_follower_snapshots_tenant_id", "follower_snapshots", ["tenant_id"])
    op.execute(f"GRANT SELECT ON follower_snapshots TO {APP}")
    # a later reading the same day replaces that day's count; history is never deleted
    op.execute(f"GRANT SELECT, INSERT, UPDATE (followers, handle) ON follower_snapshots "
               f"TO {WORKER}")
    op.execute("ALTER TABLE follower_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE follower_snapshots FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON follower_snapshots "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON follower_snapshots TO {WORKER} "
               f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.execute("DROP TABLE follower_snapshots")
