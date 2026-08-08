"""card_statements: the exact amount due on a credit card (#11)

A card's synced running balance is not what is due: it includes spending that
posted after the statement closed. A household paying in full needs the
STATEMENT balance and its due date — figures the bank feed doesn't carry — so
they are recorded per cycle, from an uploaded statement or by hand.

One row per cycle per account. The newest unpaid row is "the amount to pay".

Revision ID: 0087_card_statements
Revises: 0086_payroll_deductions
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0087_card_statements"
down_revision: str | None = "0086_payroll_deductions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "card_statements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        # Text, not BigInteger: amounts are sealed per household (ADR 0072), so
        # the column has to hold ciphertext as well as digits.
        sa.Column("statement_balance_minor", sa.Text(), nullable=False),
        sa.Column("minimum_due_minor", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        # The uploaded statement this was read from, when it came from a scan.
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=True),
        # Set when the household marks the cycle paid, or the matcher finds the
        # clearing payment. Null = still owed.
        sa.Column("paid_at", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # One statement per cycle per card: re-uploading the same cycle updates it
    # rather than stacking a second obligation for the same money.
    op.create_index(
        "uq_card_statements_account_due",
        "card_statements",
        ["account_id", "due_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_card_statements_account_due", table_name="card_statements")
    op.drop_table("card_statements")
