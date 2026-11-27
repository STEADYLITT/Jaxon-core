"""Launch approvals: one owner approval per launch, then Jackson runs inside its scope
(phase 21, ADR-032).

Revision ID: 0017_launches
Revises: 0016_dashboard
Create Date: 2026-11-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_launches"
down_revision = "0016_dashboard"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
STATUSES = ("('proposed', 'active', 'paused', 'revoked', 'completed', 'killed', 'expired', "
            "'rejected')")


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "launches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), sa.ForeignKey("opportunities.id")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.Text()),
        sa.Column("total_budget_cents", sa.BigInteger(), nullable=False),
        sa.Column("daily_cap_cents", sa.BigInteger(), nullable=False),
        sa.Column("channels", postgresql.JSONB(), nullable=False),
        sa.Column("kill_rules", postgresql.JSONB(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_launch_status"),
        sa.CheckConstraint("status IN ('proposed','rejected') OR (approved_by IS NOT NULL "
                           "AND starts_at IS NOT NULL AND ends_at > starts_at)",
                           name="ck_launch_approved"),
        sa.CheckConstraint("total_budget_cents >= 0 AND daily_cap_cents >= 0 "
                           "AND daily_cap_cents <= total_budget_cents", name="ck_launch_budget"),
    )
    op.create_table(
        "launch_signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("launch_id", sa.Uuid(), sa.ForeignKey("launches.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_ref", sa.String(300)),
        _ts("created_at"),
        sa.CheckConstraint("kind IN ('sale','refund','send','bounce','complaint',"
                           "'platform_error')", name="ck_launch_signal_kind"),
        sa.CheckConstraint("quantity >= 0 AND amount_cents >= 0",
                           name="ck_launch_signal_values"),
        sa.UniqueConstraint("launch_id", "kind", "external_ref"),
    )
    for t in ("launches", "launch_signals"):
        op.create_index(f"ix_{t}_tenant_id", t, ["tenant_id"])

    op.add_column("workflow_runs", sa.Column("launch_id", sa.Uuid()))
    op.create_foreign_key("fk_runs_launch", "workflow_runs", "launches", ["launch_id"], ["id"])
    op.add_column("content_items", sa.Column("launch_id", sa.Uuid(),
                                             sa.ForeignKey("launches.id")))
    op.add_column("outreach_campaigns", sa.Column("launch_id", sa.Uuid(),
                                                  sa.ForeignKey("launches.id")))

    # Does an ACTIVE launch of this business, inside its time window, allow this channel
    # and target? The one definition every guard below uses.
    op.execute("""
        CREATE FUNCTION launch_covers(p_launch uuid, p_business uuid, p_channel text,
                                      p_target text) RETURNS boolean
        LANGUAGE sql STABLE AS $$
          SELECT EXISTS (SELECT 1 FROM launches l WHERE l.id = p_launch
            AND l.business_id = p_business AND l.status = 'active'
            AND now() >= l.starts_at AND now() < l.ends_at
            AND CASE p_channel
                  WHEN 'social' THEN COALESCE(l.channels->'social'->'accounts', '[]') ? p_target
                  WHEN 'email' THEN COALESCE(l.channels->'email'->'lists', '[]') ? p_target
                  WHEN 'shopify_publish' THEN COALESCE((l.channels->>'shopify_publish')::boolean,
                                                       false)
                  ELSE false END)
        $$;
    """)

    # Content: published/scheduled/approved needs a person's approval OR a covering launch
    # (and a passing compliance review). Replaces the CHECK of phase 16; same name in the
    # error so the guarantee reads the same.
    op.drop_constraint("ck_content_needs_approval", "content_items", type_="check")
    op.execute("""
        CREATE FUNCTION content_needs_approval() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status IN ('approved','scheduled','published') AND NEW.approved_by IS NULL
             AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status) THEN
            IF NEW.launch_id IS NULL OR COALESCE(NEW.review->>'passed', 'false') <> 'true'
               OR NOT launch_covers(NEW.launch_id, NEW.business_id, 'social', NEW.channel) THEN
              RAISE EXCEPTION 'ck_content_needs_approval: content needs a person''s approval '
                              'or an active launch approval covering %', NEW.channel;
            END IF;
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("CREATE TRIGGER content_needs_approval BEFORE INSERT OR UPDATE ON content_items "
               "FOR EACH ROW EXECUTE FUNCTION content_needs_approval()")

    # Campaigns: same idea; the launch must name the campaign's list.
    op.drop_constraint("ck_campaign_needs_approval", "outreach_campaigns", type_="check")
    op.execute("""
        CREATE FUNCTION campaign_needs_approval() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status IN ('approved','sending','completed') AND NEW.approved_by IS NULL
             AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status)
             AND NOT (TG_OP = 'UPDATE' AND OLD.status IN ('approved','sending')) THEN
            IF NEW.launch_id IS NULL OR NOT launch_covers(
                 NEW.launch_id, NEW.business_id, 'email', NEW.audience->>'list') THEN
              RAISE EXCEPTION 'ck_campaign_needs_approval: campaign needs a person''s approval '
                              'or an active launch approval covering its list';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("CREATE TRIGGER campaign_needs_approval BEFORE INSERT OR UPDATE ON "
               "outreach_campaigns FOR EACH ROW EXECUTE FUNCTION campaign_needs_approval()")

    # A run's launch is fixed when the run is created: nobody (including the worker, which
    # may update runs) can attach a run to a launch afterwards and so widen its scope.
    op.execute("""
        CREATE FUNCTION run_launch_fixed() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.launch_id IS DISTINCT FROM OLD.launch_id THEN
            RAISE EXCEPTION 'run_launch_fixed: a run''s launch can''t change';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("CREATE TRIGGER run_launch_fixed BEFORE UPDATE ON workflow_runs "
               "FOR EACH ROW EXECUTE FUNCTION run_launch_fixed()")

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON launches TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON launch_signals TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON launches TO {WORKER}")
    # The worker can only change a launch's status (auto-pause / kill / expire), never its
    # scope, and the policy below only lets it move an ACTIVE launch to a stopped state.
    op.execute(f"GRANT UPDATE (status, status_reason, updated_at) ON launches TO {WORKER}")
    op.execute(f"GRANT SELECT, INSERT ON launch_signals TO {WORKER}")
    op.execute(f"GRANT INSERT ON outreach_campaigns TO {WORKER}")
    for t in ("launches", "launch_signals"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} "
                   f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
        op.execute(f"CREATE POLICY worker_access ON {t} TO {WORKER} "
                   f"USING (true) WITH CHECK (true)")
    op.execute(f"CREATE POLICY worker_proposes_only ON launches AS RESTRICTIVE FOR INSERT "
               f"TO {WORKER} WITH CHECK (status = 'proposed' AND approved_by IS NULL)")
    op.execute(f"CREATE POLICY worker_can_only_stop ON launches AS RESTRICTIVE FOR UPDATE "
               f"TO {WORKER} USING (status = 'active') "
               f"WITH CHECK (status IN ('active','paused','killed','completed','expired'))")
    # Jackson may draft a campaign inside a launch; approving it is the trigger's job.
    op.execute(f"CREATE POLICY worker_launch_campaigns ON outreach_campaigns AS RESTRICTIVE "
               f"FOR INSERT TO {WORKER} WITH CHECK (status = 'draft' AND approved_by IS NULL "
               f"AND launch_id IS NOT NULL)")
    op.execute("DROP POLICY worker_campaign_progress ON outreach_campaigns")
    op.execute(f"CREATE POLICY worker_campaign_progress ON outreach_campaigns AS RESTRICTIVE "
               f"FOR UPDATE TO {WORKER} USING (status IN ('draft','prepared','approved','sending'))"
               f" WITH CHECK (status IN ('prepared','sending','completed') OR "
               f"(status = 'approved' AND launch_id IS NOT NULL))")


def downgrade() -> None:
    raise NotImplementedError("restore from backup to go back before launch approvals")
