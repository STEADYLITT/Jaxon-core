"""Ads end to end (ADR-051): the ad and its creative, plus spend and results exactly as
Meta/TikTok report them.

Revision ID: 0026_ad_insights
Revises: 0025_ads
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_ad_insights"
down_revision = "0025_ads"
branch_labels = None
depends_on = None

COLS = [("external_ad_id", sa.String(64)), ("creative", postgresql.JSONB()),
        ("spend_cents_reported", sa.BigInteger()), ("impressions", sa.BigInteger()),
        ("clicks", sa.BigInteger()), ("results", sa.BigInteger()),
        ("insights_at", sa.DateTime(timezone=True))]


def upgrade() -> None:
    for name, typ in COLS:
        op.add_column("ad_campaigns", sa.Column(name, typ))
    op.execute("GRANT UPDATE (" + ", ".join(n for n, _ in COLS) + ") ON ad_campaigns "
               "TO jackson_worker")


def downgrade() -> None:
    for name, _ in reversed(COLS):
        op.drop_column("ad_campaigns", name)
