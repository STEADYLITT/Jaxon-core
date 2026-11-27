"""Business lines (ADR-039): Jackson is the CEO of all of Steady's lines.

- businesses.line: which line a business belongs to (owner-set; seeded for the owner);
- line_playbooks: the versioned playbook + monthly budget per line (owner edits);
- the City's generic departments are replaced by the 8 lines: old department/floor rows
  are removed (agents are re-placed by the registry on the next heartbeat; past events
  keep their history, they only reference departments by id string).

Revision ID: 0020_business_lines
Revises: 0019_city_guards
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_business_lines"
down_revision = "0019_city_guards"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
LINES = ("('rnd','stu','agency','clips','creators','influencers','ecommerce','finance')")


def upgrade() -> None:
    op.add_column("businesses", sa.Column("line", sa.String(32)))
    op.create_check_constraint("ck_business_line", "businesses",
                               f"line IS NULL OR line IN {LINES}")
    op.create_table(
        "line_playbooks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("line", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("body", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("budget_monthly_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("tenant_id", "line"),
        sa.CheckConstraint(f"line IN {LINES}", name="ck_playbook_line"),
        sa.CheckConstraint("budget_monthly_cents >= 0", name="ck_playbook_budget"),
    )
    op.create_index("ix_line_playbooks_tenant_id", "line_playbooks", ["tenant_id"])
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON line_playbooks TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON line_playbooks TO {WORKER}")
    op.execute("ALTER TABLE line_playbooks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE line_playbooks FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON line_playbooks "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON line_playbooks TO {WORKER} "
               f"USING (true) WITH CHECK (true)")
    # The registry may re-place an agent when its line changes (never its status holds).
    op.execute(f"GRANT UPDATE (department_id, floor_id, name, role) ON agents TO {APP}, "
               f"{WORKER}")
    # Replace the generic departments with the business lines.
    op.execute("UPDATE agents SET department_id = NULL, floor_id = NULL")
    op.execute("DELETE FROM floors")
    op.execute("DELETE FROM departments")


def downgrade() -> None:
    op.drop_table("line_playbooks")
    op.drop_constraint("ck_business_line", "businesses")
    op.drop_column("businesses", "line")
