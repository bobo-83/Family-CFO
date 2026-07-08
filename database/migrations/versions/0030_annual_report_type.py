"""annual report type

Revision ID: 0030_annual_report_type
Revises: 0029_account_debt_terms
Create Date: 2026-07-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030_annual_report_type"
down_revision: str | None = "0029_account_debt_terms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_REPORT_TYPES = ("weekly", "monthly")
NEW_REPORT_TYPES = ("weekly", "monthly", "annual")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    with op.batch_alter_table("reports") as batch:
        batch.drop_constraint("ck_reports_type", type_="check")
        batch.create_check_constraint("ck_reports_type", f"report_type in {_sql_in(NEW_REPORT_TYPES)}")


def downgrade() -> None:
    with op.batch_alter_table("reports") as batch:
        batch.drop_constraint("ck_reports_type", type_="check")
        batch.create_check_constraint("ck_reports_type", f"report_type in {_sql_in(OLD_REPORT_TYPES)}")
