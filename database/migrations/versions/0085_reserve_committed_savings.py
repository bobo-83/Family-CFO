"""households.reserve_committed_savings: reserve savings like a bill (#5)

By default committed savings are shown beside Safe to Spend, not subtracted —
a savings transfer is self-imposed and skippable, unlike a mortgage. A
household may opt to reserve it like a bill; this flag records that choice.
Null = false = informational.

Revision ID: 0085_reserve_committed_savings
Revises: 0084_contribution_goal_link
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0085_reserve_committed_savings"
down_revision: str | None = "0084_contribution_goal_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "households",
        sa.Column("reserve_committed_savings", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("households", "reserve_committed_savings")
