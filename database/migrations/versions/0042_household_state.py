"""household state for state income tax (M65)

Revision ID: 0042_household_state
Revises: 0041_income_analysis
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_household_state"
down_revision: str | None = "0041_income_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only the state is collected — the tax estimate has no use for a street
    # address, and this app does not over-collect (privacy-first).
    op.add_column("households", sa.Column("state", sa.String(2), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "state")
