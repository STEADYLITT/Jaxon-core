"""The City command center (phase 23, ADR-037): agent registry (departments, floors,
agents) and one append-only `events` table that every real agent action writes to.

Reuses existing tables: commands = workflow_runs, tasks = tasks, approvals = approvals,
spend = budget ledger + model_usage. Nothing here is ever filled with made-up rows.

Revision ID: 0018_city
Revises: 0017_launches
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_city"
down_revision = "0017_launches"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
AGENT_STATUSES = "('idle','working','done','waiting','failed','offline','paused','killed')"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("color", sa.String(16), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("tenant_id", "key"),
    )
    op.create_table(
        "floors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("department_id", sa.Uuid(), sa.ForeignKey("departments.id"), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("tenant_id", "department_id", "key"),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        # "kind:<task kind>" for built-in workers, "spec:<name>" for AgentSpec agents
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(200), nullable=False),
        sa.Column("task_kind", sa.String(64), nullable=False),
        sa.Column("department_id", sa.Uuid(), sa.ForeignKey("departments.id")),
        sa.Column("floor_id", sa.Uuid(), sa.ForeignKey("floors.id")),
        sa.Column("status", sa.String(16), nullable=False, server_default="idle"),
        sa.Column("model", sa.String(100)),
        sa.Column("current_task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        _ts("created_at"),
        sa.UniqueConstraint("tenant_id", "key"),
        sa.CheckConstraint(f"status IN {AGENT_STATUSES}", name="ck_agent_status_city"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        _ts("ts"),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("from_kind", sa.String(16)),
        sa.Column("from_id", sa.String(100)),
        sa.Column("to_kind", sa.String(16)),
        sa.Column("to_id", sa.String(100)),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id")),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("tasks.id")),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id")),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_events_tenant_id_id", "events", ["tenant_id", "id"])
    op.create_index("ix_events_ts", "events", ["ts"])
    op.create_index("ix_events_agent_ts", "events", ["agent_id", "ts"])
    op.create_index("ix_events_task", "events", ["task_id"])
    for t in ("departments", "floors", "agents"):
        op.create_index(f"ix_{t}_tenant_id", t, ["tenant_id"])

    # The API (owner) reads everything, registers agents, writes owner events (commands,
    # approvals, pause/kill) and marks agents offline when the worker stops heartbeating.
    op.execute(f"GRANT SELECT, INSERT ON departments, floors, agents, events TO {APP}")
    op.execute(f"GRANT UPDATE (status, status_changed_at, current_task_id) ON agents TO {APP}")
    # The worker registers agents, reports status/heartbeats and writes events. It cannot
    # un-pause or un-kill an agent: a trigger below refuses that for the worker role.
    op.execute(f"GRANT SELECT, INSERT ON departments, floors, agents, events TO {WORKER}")
    op.execute(f"GRANT UPDATE (status, status_changed_at, current_task_id, last_heartbeat_at, "
               f"model) ON agents TO {WORKER}")
    op.execute(f"GRANT USAGE ON SEQUENCE events_id_seq TO {APP}, {WORKER}")
    for t in ("departments", "floors", "agents", "events"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} "
                   f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
        op.execute(f"CREATE POLICY worker_access ON {t} TO {WORKER} "
                   f"USING (true) WITH CHECK (true)")
    op.execute("""
        CREATE FUNCTION agent_owner_holds() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF current_user = 'jackson_worker' AND OLD.status IN ('paused','killed')
             AND NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'agent_owner_hold: only the owner can resume a % agent', OLD.status
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("CREATE TRIGGER agent_owner_holds BEFORE UPDATE ON agents "
               "FOR EACH ROW EXECUTE FUNCTION agent_owner_holds()")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS agent_owner_holds ON agents")
    op.execute("DROP FUNCTION IF EXISTS agent_owner_holds()")
    op.drop_table("events")
    op.drop_table("agents")
    op.drop_table("floors")
    op.drop_table("departments")
