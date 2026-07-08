"""future_value calculation type

Revision ID: 0031_future_value_calc_type
Revises: 0030_annual_report_type
Create Date: 2026-07-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031_future_value_calc_type"
down_revision: str | None = "0030_annual_report_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CALCULATION_TYPES = (
    "net_worth",
    "cash_flow",
    "budget_summary",
    "emergency_fund",
    "goal_progress",
    "purchase_impact",
    "debt_payoff",
    "retirement_projection",
)
NEW_CALCULATION_TYPES = (*OLD_CALCULATION_TYPES, "future_value")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
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
