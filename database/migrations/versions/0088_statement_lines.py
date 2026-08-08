"""card_statement_lines: the statement's own transactions (#25)

A statement balance answers "what do I owe". Its LINE ITEMS answer the more
important question — "is my synced ledger complete?" — by naming the charges
the bank feed never delivered. Each line is matched to a synced transaction
where one exists; an unmatched line is the interesting case, not an error.

Matching is stored (not recomputed on read) so a match survives, can be shown
with its confidence, and can be undone.

Revision ID: 0088_statement_lines
Revises: 0087_card_statements
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0088_statement_lines"
down_revision: str | None = "0087_card_statements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "card_statement_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column(
            "statement_id",
            sa.String(36),
            sa.ForeignKey("card_statements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        # Text: sealed per household (ADR 0072) like every other description
        # and amount.
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("amount_minor", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        # The synced transaction this line was matched to, if any. NULL is the
        # meaningful case: the feed never delivered this charge.
        sa.Column(
            "matched_transaction_id",
            sa.String(36),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # "exact" | "amount_differs" — recorded so the UI can distinguish "this
        # is the same charge" from "the same charge for a different amount".
        sa.Column("match_kind", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_card_statement_lines_statement", "card_statement_lines", ["statement_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_card_statement_lines_statement", table_name="card_statement_lines")
    op.drop_table("card_statement_lines")
