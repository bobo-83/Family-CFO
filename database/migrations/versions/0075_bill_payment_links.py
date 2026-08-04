"""bill_payment_links: point a bill occurrence at the transaction that paid it

Auto-matching pairs a bill with its charge by merchant + due-window with a
±30% amount tolerance (ADR 0024). Variable-amount bills (net-metered
electric swinging past the tolerance, renamed merchants) can miss — this
link is the user's explicit receipt, and it wins over the matcher.

Revision ID: 0075_bill_payment_links
Revises: 0074_rsu_ready_to_sell
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0075_bill_payment_links"
down_revision: str | None = "0074_rsu_ready_to_sell"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bill_payment_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False
        ),
        sa.Column("bill_id", sa.String(36), sa.ForeignKey("bills.id"), nullable=False),
        sa.Column(
            "transaction_id", sa.String(36), sa.ForeignKey("transactions.id"), nullable=False
        ),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bill_id", "due_date", name="uq_bill_payment_links_bill_due"),
        sa.UniqueConstraint("transaction_id", name="uq_bill_payment_links_transaction"),
    )


def downgrade() -> None:
    op.drop_table("bill_payment_links")
