"""Community (ADR-044): remember what Jackson last did for each Whop member on Discord and
Telegram, so a sync only touches members whose access changed.

Revision ID: 0024_community
Revises: 0023_web_research
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_community"
down_revision = "0023_web_research"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("whop_members", sa.Column("discord_synced", sa.String(16)))
    op.add_column("whop_members", sa.Column("telegram_synced", sa.String(16)))


def downgrade() -> None:
    op.drop_column("whop_members", "telegram_synced")
    op.drop_column("whop_members", "discord_synced")
