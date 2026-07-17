"""credit_cards_paid_in_full preference (M96)

Bank sync sends card balances but not the statement minimum or due date, so a
household that pays its cards in full each month had its whole card balance
treated as long-term debt and $0 of it counted as committed against
safe-to-spend. This flag lets safe-to-spend treat the full card balances as
money about to leave liquid cash. Null/false keeps the old minimum-payment
behaviour.

Additive: no data is rewritten.

Revision ID: 0046_credit_cards_paid_in_full
Revises: 0045_safe_to_spend_calculation
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_credit_cards_paid_in_full"
down_revision: str | None = "0045_safe_to_spend_calculation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "households",
        sa.Column("credit_cards_paid_in_full", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("households", "credit_cards_paid_in_full")
