"""CRM and compliant outreach (phase 18, ADR-028).

Revision ID: 0015_crm
Revises: 0014_sites
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_crm"
down_revision = "0014_sites"
branch_labels = None
depends_on = None

APP, WORKER = "jackson_app", "jackson_worker"
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
SOURCES = "('inbound', 'referral', 'existing_customer', 'event', 'public_business_listing')"
BASES = "('consent', 'legitimate_interest', 'existing_relationship')"
STAGES = "('new', 'contacted', 'replied', 'meeting', 'proposal', 'won', 'lost')"
CHANNELS = "('email', 'sms', 'call')"
TABLES = ("contacts", "suppressions", "outreach_campaigns", "outreach_messages")


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _base() -> list:
    return [sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "contacts", *_base(),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(32)),
        sa.Column("region", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("industry", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("lawful_basis", sa.String(32), nullable=False),
        sa.Column("sms_consent_at", sa.DateTime(timezone=True)),
        sa.Column("call_consent_at", sa.DateTime(timezone=True)),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("score_detail", postgresql.JSONB(), nullable=False),
        sa.Column("dedupe_key", sa.String(400), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        _ts("created_at"), _ts("updated_at"),
        sa.UniqueConstraint("tenant_id", "dedupe_key"),
        sa.CheckConstraint(f"source_type IN {SOURCES}", name="ck_contact_source"),
        sa.CheckConstraint(f"lawful_basis IN {BASES}", name="ck_contact_basis"),
        sa.CheckConstraint(f"stage IN {STAGES}", name="ck_contact_stage"),
        sa.CheckConstraint("length(source_ref) > 0", name="ck_contact_source_ref"),
    )
    op.create_table(
        "suppressions", *_base(),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("value_hash", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("tenant_id", "channel", "value_hash"),
        sa.CheckConstraint("channel IN ('email','sms','call','all')",
                           name="ck_suppression_channel"),
    )
    op.create_table(
        "outreach_campaigns", *_base(),
        sa.Column("business_id", sa.Uuid(), sa.ForeignKey("businesses.id")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("sender_name", sa.String(200), nullable=False),
        sa.Column("sender_email", sa.String(320), nullable=False),
        sa.Column("postal_address", sa.String(500), nullable=False),
        sa.Column("subject_template", sa.String(300), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("audience", postgresql.JSONB(), nullable=False),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        _ts("created_at"), _ts("updated_at"),
        sa.CheckConstraint(f"channel IN {CHANNELS}", name="ck_campaign_channel"),
        sa.CheckConstraint("status IN ('draft','prepared','approved','sending','completed',"
                           "'cancelled')", name="ck_campaign_status"),
        sa.CheckConstraint("status NOT IN ('approved','sending','completed') "
                           "OR approved_by IS NOT NULL", name="ck_campaign_needs_approval"),
        sa.CheckConstraint("daily_limit BETWEEN 1 AND 500", name="ck_campaign_daily_limit"),
    )
    op.create_table(
        "outreach_messages", *_base(),
        sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("outreach_campaigns.id"),
                  nullable=False),
        sa.Column("contact_id", sa.Uuid(), sa.ForeignKey("contacts.id", ondelete="SET NULL")),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("to_address", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("review", postgresql.JSONB(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        _ts("created_at"), _ts("updated_at"),
        sa.UniqueConstraint("campaign_id", "contact_id"),
        sa.CheckConstraint("status IN ('blocked','queued','sending','sent','failed','skipped')",
                           name="ck_message_status"),
    )
    for t in TABLES:
        op.create_index(f"ix_{t}_tenant_id", t, ["tenant_id"])

    # The last line of defence: whoever writes the row, a message can only be claimed for
    # sending ('sending') if its campaign is approved, the address isn't suppressed, and
    # SMS/calls have consent. 'sent' can only follow 'sending': once the provider has the
    # message, recording that it went out is always allowed (the record must match reality).
    op.execute("""
        CREATE FUNCTION outreach_send_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE c record;
        BEGIN
          IF NEW.status = 'sent' THEN
            IF TG_OP = 'UPDATE' AND OLD.status IN ('sending', 'sent') THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION 'outreach_blocked: a message must be claimed (sending) first';
          END IF;
          IF NEW.status <> 'sending' OR (TG_OP = 'UPDATE' AND OLD.status = 'sending') THEN
            RETURN NEW;
          END IF;
          SELECT status INTO c FROM outreach_campaigns WHERE id = NEW.campaign_id;
          IF c.status NOT IN ('approved', 'sending') THEN
            RAISE EXCEPTION 'outreach_blocked: campaign is %', c.status;
          END IF;
          IF EXISTS (SELECT 1 FROM suppressions s WHERE s.tenant_id = NEW.tenant_id
                     AND s.channel IN (NEW.channel, 'all')
                     AND s.value_hash = encode(sha256(convert_to(NEW.to_address, 'UTF8')),
                                               'hex')) THEN
            RAISE EXCEPTION 'outreach_blocked: recipient opted out';
          END IF;
          IF NEW.channel = 'sms' AND NOT EXISTS (SELECT 1 FROM contacts WHERE
                 id = NEW.contact_id AND sms_consent_at IS NOT NULL) THEN
            RAISE EXCEPTION 'outreach_blocked: no recorded SMS consent';
          END IF;
          IF NEW.channel = 'call' AND NOT EXISTS (SELECT 1 FROM contacts WHERE
                 id = NEW.contact_id AND call_consent_at IS NOT NULL) THEN
            RAISE EXCEPTION 'outreach_blocked: no recorded call consent';
          END IF;
          RETURN NEW;
        END $$;
    """)
    op.execute("CREATE TRIGGER outreach_send_guard BEFORE INSERT OR UPDATE ON "
               "outreach_messages FOR EACH ROW EXECUTE FUNCTION outreach_send_guard()")

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON contacts TO {APP}")
    op.execute(f"GRANT SELECT, INSERT ON suppressions TO {APP}")  # never UPDATE/DELETE
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON outreach_campaigns TO {APP}")
    op.execute(f"GRANT SELECT ON outreach_messages TO {APP}")
    op.execute(f"GRANT SELECT ON contacts TO {WORKER}")
    op.execute(f"GRANT UPDATE (stage, updated_at) ON contacts TO {WORKER}")
    op.execute(f"GRANT SELECT, INSERT ON suppressions TO {WORKER}")
    op.execute(f"GRANT SELECT ON outreach_campaigns TO {WORKER}")
    op.execute(f"GRANT UPDATE (status, updated_at) ON outreach_campaigns TO {WORKER}")
    op.execute(f"GRANT SELECT, INSERT ON outreach_messages TO {WORKER}")
    op.execute(f"GRANT UPDATE (status, review, external_id, sent_at, updated_at) "
               f"ON outreach_messages TO {WORKER}")
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} "
                   f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})")
        op.execute(f"CREATE POLICY worker_access ON {t} TO {WORKER} "
                   f"USING (true) WITH CHECK (true)")
    # The worker can't approve or un-cancel campaigns: it may only move them along
    # draft -> prepared and approved -> sending -> completed. It can't write approved_by,
    # and the CHECK above needs approved_by for sending/completed.
    op.execute(f"CREATE POLICY worker_campaign_progress ON outreach_campaigns AS RESTRICTIVE "
               f"FOR UPDATE TO {WORKER} USING (status IN ('draft','approved','sending')) "
               f"WITH CHECK (status IN ('prepared','sending','completed'))")
    # The worker can only create messages that are queued or blocked, never already sent.
    op.execute(f"CREATE POLICY worker_messages_unsent ON outreach_messages AS RESTRICTIVE "
               f"FOR INSERT TO {WORKER} WITH CHECK (status IN ('queued','blocked','skipped'))")


def downgrade() -> None:
    op.execute("DROP TABLE outreach_messages, outreach_campaigns, suppressions, contacts")
    op.execute("DROP FUNCTION IF EXISTS outreach_send_guard()")
