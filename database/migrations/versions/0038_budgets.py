"""budget envelopes: monthly per-category limits (M46)

Revision ID: 0038_budgets
Revises: 0037_category_unique_name
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_budgets"
down_revision: str | None = "0037_category_unique_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column(
            "category_id",
            sa.String(36),
            sa.ForeignKey("transaction_categories.id"),
            nullable=False,
        ),
        sa.Column("limit_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # One envelope per category per household.
    op.create_index(
        "uq_budgets_household_category",
        "budgets",
        ["household_id", "category_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_budgets_household_category", table_name="budgets")
    op.drop_table("budgets")
