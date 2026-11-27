"""Course Factory (ADR-040): one row per course build with its outline, lessons, the
student platform and sales page drafts, their check results and the proposed launch.

Revision ID: 0021_courses
Revises: 0020_business_lines
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_courses"
down_revision = "0020_business_lines"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id")),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("price", sa.String(16)),
        sa.Column("outline", postgresql.JSONB(), nullable=False),
        sa.Column("lessons", postgresql.JSONB(), nullable=False),
        sa.Column("platform_files", postgresql.JSONB(), nullable=False),
        sa.Column("sales_files", postgresql.JSONB(), nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        sa.Column("launch_id", sa.Uuid(), sa.ForeignKey("launches.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.CheckConstraint("status IN ('ready','needs_fixes')", name="ck_course_status"),
        sa.CheckConstraint("mode IN ('live','mock')", name="ck_course_mode"),
    )
    op.create_index("ix_courses_tenant_id", "courses", ["tenant_id"])
    # A build is immutable: a new build is a new row (like site versions).
    op.execute(f"GRANT SELECT ON courses TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON courses TO {WORKER}")
    op.execute("ALTER TABLE courses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE courses FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON courses "
               f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
    op.execute(f"CREATE POLICY worker_access ON courses TO {WORKER} "
               f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.execute("DROP TABLE courses")
