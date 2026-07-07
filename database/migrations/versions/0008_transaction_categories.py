"""transaction_categories

Revision ID: 0008_transaction_categories
Revises: 0007_account_balances
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_transaction_categories"
down_revision: str | None = "0007_account_balances"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transaction_categories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column(
            "parent_category_id",
            sa.String(36),
            sa.ForeignKey("transaction_categories.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("transaction_categories")
