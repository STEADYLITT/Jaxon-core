"""Let research attach economics snapshots to opportunities (phase 14, ADR-024).

Revision ID: 0011_economics
Revises: 0010_opportunities
Create Date: 2026-11-26
"""

from alembic import op

revision = "0011_economics"
down_revision = "0010_opportunities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Still no right to change `status`: that stays the owner's decision.
    op.execute("GRANT UPDATE (economics) ON opportunities TO jackson_worker")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (economics) ON opportunities FROM jackson_worker")
