"""account debt terms and new calculation types

Revision ID: 0029_account_debt_terms
Revises: 0028_conversation_messages
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_account_debt_terms"
down_revision: str | None = "0028_conversation_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CALCULATION_TYPES = (
    "net_worth",
    "cash_flow",
    "budget_summary",
    "emergency_fund",
    "goal_progress",
    "purchase_impact",
)
NEW_CALCULATION_TYPES = (*OLD_CALCULATION_TYPES, "debt_payoff", "retirement_projection")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.add_column("accounts", sa.Column("annual_interest_rate", sa.Float, nullable=True))
    op.add_column("accounts", sa.Column("minimum_payment_minor", sa.BigInteger, nullable=True))

    with op.batch_alter_table("financial_calculations") as batch:
        batch.drop_constraint("ck_financial_calculations_type", type_="check")
        batch.create_check_constraint(
            "ck_financial_calculations_type",
            f"calculation_type in {_sql_in(NEW_CALCULATION_TYPES)}",
        )


def downgrade() -> None:
    with op.batch_alter_table("financial_calculations") as batch:
        batch.drop_constraint("ck_financial_calculations_type", type_="check")
        batch.create_check_constraint(
            "ck_financial_calculations_type",
            f"calculation_type in {_sql_in(OLD_CALCULATION_TYPES)}",
        )

    op.drop_column("accounts", "minimum_payment_minor")
    op.drop_column("accounts", "annual_interest_rate")
