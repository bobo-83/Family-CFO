"""monthly Overview snapshots (M96)

Most Overview cards (safe-to-spend, emergency fund, upcoming bills) are computed
from CURRENT balances and have no stored history. To let the user look back at a
past month and see the whole page as it was, snapshot the full HouseholdContext
per household per month. History builds from now — past months before the first
snapshot can't be reconstructed.

Additive: no data is rewritten.

Revision ID: 0049_overview_snapshots
Revises: 0048_account_maturity_date
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_overview_snapshots"
down_revision: str | None = "0048_account_maturity_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "overview_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id",
            sa.String(36),
            sa.ForeignKey("households.id"),
            nullable=False,
        ),
        sa.Column("month", sa.String(7), nullable=False),  # "YYYY-MM"
        sa.Column("snapshot", sa.Text(), nullable=False),  # serialized HouseholdContext
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "month", name="uq_overview_snapshots_household_month"),
    )


def downgrade() -> None:
    op.drop_table("overview_snapshots")
