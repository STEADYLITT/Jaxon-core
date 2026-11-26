"""Website factory: sites and immutable site versions (phase 17, ADR-027).

Revision ID: 0014_sites
Revises: 0013_content
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_sites"
down_revision = "0013_content"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
STATUSES = "('draft', 'ready', 'live')"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("requirements", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("domain", sa.String(253)),
        sa.Column("domain_token", sa.String(64)),
        sa.Column("live_version_id", sa.Uuid()),
        sa.Column("previous_version_id", sa.Uuid()),
        sa.Column("live_url", sa.String(500)),
        sa.Column("launched_at", sa.DateTime(timezone=True)),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_site_status"),
    )
    op.create_index("ix_sites_tenant_id", "sites", ["tenant_id"])
    op.create_table(
        "site_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("site_id", sa.Uuid(), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("workflow_runs.id")),
        sa.Column("files", postgresql.JSONB(), nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("site_id", "number"),
    )
    op.create_index("ix_site_versions_tenant_id", "site_versions", ["tenant_id"])
    op.create_foreign_key("fk_sites_live_version", "sites", "site_versions",
                          ["live_version_id"], ["id"])
    op.create_foreign_key("fk_sites_previous_version", "sites", "site_versions",
                          ["previous_version_id"], ["id"])

    # Only a version of THIS site that passed its tests can go live (or be kept for
    # rollback), whoever writes the row: API, worker or a person with SQL access.
    op.execute("""
        CREATE FUNCTION sites_only_tested_versions() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE v_id uuid;
        BEGIN
          FOREACH v_id IN ARRAY ARRAY[NEW.live_version_id, NEW.previous_version_id] LOOP
            IF v_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM site_versions
                WHERE id = v_id AND site_id = NEW.id AND passed) THEN
              RAISE EXCEPTION 'site_version_not_tested: version % did not pass its tests', v_id;
            END IF;
          END LOOP;
          IF NEW.status = 'live' AND NEW.live_version_id IS NULL THEN
            RAISE EXCEPTION 'site_version_not_tested: a live site needs a tested version';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("CREATE TRIGGER sites_only_tested_versions BEFORE INSERT OR UPDATE ON sites "
               "FOR EACH ROW EXECUTE FUNCTION sites_only_tested_versions()")
    # Versions are immutable: nobody may edit a version's files or results after the fact.
    op.execute("""
        CREATE FUNCTION site_versions_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'site_versions are immutable'; END $$;
    """)
    op.execute("CREATE TRIGGER site_versions_immutable BEFORE UPDATE ON site_versions "
               "FOR EACH ROW EXECUTE FUNCTION site_versions_immutable()")

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON sites TO {APP}")
    op.execute(f"GRANT SELECT ON site_versions TO {APP}")
    op.execute(f"GRANT SELECT ON sites TO {WORKER}")
    op.execute(f"GRANT UPDATE (status, live_version_id, previous_version_id, live_url, "
               f"launched_at, updated_at) ON sites TO {WORKER}")
    op.execute(f"GRANT SELECT, INSERT ON site_versions TO {WORKER}")
    for table in ("sites", "site_versions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
        op.execute(f"CREATE POLICY worker_access ON {table} TO {WORKER} "
                   f"USING (true) WITH CHECK (true)")


def downgrade() -> None:
    op.execute("DROP TABLE site_versions, sites CASCADE")
    op.execute("DROP FUNCTION IF EXISTS sites_only_tested_versions()")
    op.execute("DROP FUNCTION IF EXISTS site_versions_immutable()")
