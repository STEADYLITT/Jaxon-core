"""Web research (ADR-043): the worker may add leads from public business listings
(Google Maps places via Apify). It still can't edit or delete contacts.

Revision ID: 0023_web_research
Revises: 0022_whop
Create Date: 2026-11-28
"""

from alembic import op

revision = "0023_web_research"
down_revision = "0022_whop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT INSERT ON contacts TO jackson_worker")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON contacts FROM jackson_worker")
