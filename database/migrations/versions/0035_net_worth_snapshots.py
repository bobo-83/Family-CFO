"""net-worth history snapshots (M40)

Revision ID: 0035_net_worth_snapshots
Revises: 0034_emergency_fund
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_net_worth_snapshots"
down_revision: str | None = "0034_emergency_fund"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "net_worth_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False
        ),
        sa.Column("as_of", sa.Date, nullable=False),
        sa.Column("net_worth_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # At most one snapshot per household per day (upsert target).
    op.create_index(
        "uq_net_worth_snapshots_household_day",
        "net_worth_snapshots",
        ["household_id", "as_of"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_net_worth_snapshots_household_day", table_name="net_worth_snapshots")
    op.drop_table("net_worth_snapshots")
