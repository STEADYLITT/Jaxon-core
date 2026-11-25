"""Durable task queue: lease/retry columns on tasks + least-privilege worker role.

Revision ID: 0003_worker
Revises: 0002_runs
Create Date: 2026-11-25

ADR-016: a Postgres-backed queue (SELECT ... FOR UPDATE SKIP LOCKED + leases)
instead of Temporal. The worker connects as `jackson_worker`, which can see
queue tables across tenants (it must find work everywhere) but cannot delete
anything, cannot touch users/memberships, and can only append history.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_worker"
down_revision = "0002_runs"
branch_labels = None
depends_on = None

WORKER = "jackson_worker"
CROSS_TENANT = ("workflow_runs", "tasks", "run_events", "audit_events", "businesses")


def upgrade() -> None:
    op.add_column("tasks", sa.Column("attempts", sa.Integer(), nullable=False,
                                     server_default="0"))
    op.add_column("tasks", sa.Column("max_attempts", sa.Integer(), nullable=False,
                                     server_default="3"))
    op.add_column("tasks", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("lease_owner", sa.String(200)))
    op.add_column("tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("output", postgresql.JSONB()))
    op.add_column("tasks", sa.Column("error", sa.Text()))
    op.add_column("tasks", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("tasks", sa.Column("finished_at", sa.DateTime(timezone=True)))
    # Server defaults only backfill existing rows; the ORM supplies values afterwards.
    op.alter_column("tasks", "attempts", server_default=None)
    op.alter_column("tasks", "max_attempts", server_default=None)
    op.create_index("ix_tasks_claim", "tasks", ["status", "next_attempt_at"])

    op.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{WORKER}') "
        f"THEN CREATE ROLE {WORKER} NOLOGIN; END IF; END $$"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {WORKER}")
    op.execute(f"GRANT SELECT, UPDATE ON workflow_runs, tasks TO {WORKER}")
    op.execute(f"GRANT INSERT ON tasks TO {WORKER}")
    op.execute(f"GRANT SELECT, INSERT ON run_events, audit_events TO {WORKER}")
    op.execute(f"GRANT SELECT ON businesses, tenants, alembic_version TO {WORKER}")
    op.execute(f"GRANT USAGE ON SEQUENCE run_events_id_seq TO {WORKER}")
    for table in CROSS_TENANT:
        op.execute(
            f"CREATE POLICY worker_access ON {table} TO {WORKER} USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    for table in CROSS_TENANT:
        op.execute(f"DROP POLICY worker_access ON {table}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {WORKER}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {WORKER}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {WORKER}")
    op.drop_index("ix_tasks_claim", table_name="tasks")
    for col in ("finished_at", "started_at", "error", "output", "lease_expires_at",
                "lease_owner", "next_attempt_at", "max_attempts", "attempts"):
        op.drop_column("tasks", col)
