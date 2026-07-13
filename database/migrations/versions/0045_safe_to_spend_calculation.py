"""allow the safe_to_spend calculation type

The advisor answered "how much can I spend?" with liquid cash minus the
emergency fund, ignoring bills about to fall due and minimum debt payments —
overstating the answer by exactly what the family owed. The fix is a
deterministic `safe_to_spend` calculation, which like every other calculation is
persisted to `financial_calculations` for audit; that table's CHECK constraint
allowlists the calculation types, so it has to learn the new one.

Additive: no data is rewritten, and the old types remain valid.

Revision ID: 0045_safe_to_spend_calculation
Revises: 0044_widen_pairing_session_id
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0045_safe_to_spend_calculation"
down_revision: str | None = "0044_widen_pairing_session_id"
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
    "future_value",
)

NEW_CALCULATION_TYPES = OLD_CALCULATION_TYPES + ("safe_to_spend",)


def _sql_in(values: Sequence[str]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    with op.batch_alter_table("financial_calculations") as batch:
        batch.drop_constraint("ck_financial_calculations_type", type_="check")
        batch.create_check_constraint(
            "ck_financial_calculations_type",
            f"calculation_type in {_sql_in(NEW_CALCULATION_TYPES)}",
        )


def downgrade() -> None:
    # Rows of the dropped type would violate the restored constraint.
    op.execute("DELETE FROM financial_calculations WHERE calculation_type = 'safe_to_spend'")
    with op.batch_alter_table("financial_calculations") as batch:
        batch.drop_constraint("ck_financial_calculations_type", type_="check")
        batch.create_check_constraint(
            "ck_financial_calculations_type",
            f"calculation_type in {_sql_in(OLD_CALCULATION_TYPES)}",
        )
