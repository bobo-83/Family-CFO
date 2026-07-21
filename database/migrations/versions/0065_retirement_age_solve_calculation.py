"""allow the retirement_age_solve calculation type

"When can I retire?" was answered by asking the user for a target retirement
age — inverting their question. The new deterministic `retirement_age_solve`
calculation finds the earliest age at which savings reach 25x annual spending
(the 4% rule); like every calculation it is persisted to
`financial_calculations` for audit, so the table's CHECK allowlist has to learn
the new type.

Additive: no data is rewritten, and the old types remain valid.

Revision ID: 0065_retirement_age_solve
Revises: 0064_household_invites
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0065_retirement_age_solve"
down_revision: str | None = "0064_household_invites"
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
    "safe_to_spend",
)

NEW_CALCULATION_TYPES = OLD_CALCULATION_TYPES + ("retirement_age_solve",)


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
    op.execute(
        "DELETE FROM financial_calculations WHERE calculation_type = 'retirement_age_solve'"
    )
    with op.batch_alter_table("financial_calculations") as batch:
        batch.drop_constraint("ck_financial_calculations_type", type_="check")
        batch.create_check_constraint(
            "ck_financial_calculations_type",
            f"calculation_type in {_sql_in(OLD_CALCULATION_TYPES)}",
        )
