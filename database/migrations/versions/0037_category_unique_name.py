"""unique category name per household (M45)

Revision ID: 0037_category_unique_name
Revises: 0036_emergency_fund_target
Create Date: 2026-07-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0037_category_unique_name"
down_revision: str | None = "0036_emergency_fund_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_transaction_categories_household_name",
        "transaction_categories",
        ["household_id", "name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_transaction_categories_household_name",
        table_name="transaction_categories",
    )
