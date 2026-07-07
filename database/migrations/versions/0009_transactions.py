"""transactions

Revision ID: 0009_transactions
Revises: 0008_transaction_categories
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_transactions"
down_revision: str | None = "0008_transaction_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRANSACTION_REVIEW_STATES = ("pending", "reviewed")


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("occurred_at", sa.Date, nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("merchant", sa.String(120), nullable=True),
        sa.Column(
            "category_id", sa.String(36), sa.ForeignKey("transaction_categories.id"), nullable=True
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("import_source", sa.String(30), nullable=True),
        sa.Column("review_state", sa.String(20), nullable=False, server_default="reviewed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"review_state in {TRANSACTION_REVIEW_STATES!r}", name="ck_transactions_review_state"
        ),
    )


def downgrade() -> None:
    op.drop_table("transactions")
