"""account next payment due date

ADR 0033: a liability account can carry the next payment due date read off an
uploaded statement (or set by hand), so the Bills timeline shows a real due date
for a loan instead of guessing from payment history — or "Due date unknown".

Revision ID: 0059_account_next_payment_due
Revises: 0058_goal_contribution
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_account_next_payment_due"
down_revision: str | None = "0058_goal_contribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("next_payment_due_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "next_payment_due_date")
