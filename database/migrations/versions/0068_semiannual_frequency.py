"""allow the semiannual recurring frequency

Town utilities (sewer, water) and many insurance premiums bill every six
months; the frequency allowlist jumped from quarterly to annual, forcing
users to misfile them. The bills and income_sources CHECK constraints
learn "semiannual" (6-month cadence, engine factor 1/6 per month).

Additive: no data is rewritten, existing frequencies remain valid.

Revision ID: 0068_semiannual_frequency
Revises: 0067_yearly_reviews
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0068_semiannual_frequency"
down_revision: str | None = "0067_yearly_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_FREQUENCIES = ("weekly", "biweekly", "semimonthly", "monthly", "quarterly", "annual")
NEW_FREQUENCIES = (
    "weekly", "biweekly", "semimonthly", "monthly", "quarterly", "semiannual", "annual"
)

_CONSTRAINTS = (
    ("bills", "ck_bills_frequency"),
    ("income_sources", "ck_income_sources_frequency"),
)


def _in(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"frequency in ({joined})"


def upgrade() -> None:
    for table, constraint in _CONSTRAINTS:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="check")
            batch.create_check_constraint(constraint, _in(NEW_FREQUENCIES))


def downgrade() -> None:
    for table, constraint in _CONSTRAINTS:
        op.execute(f"DELETE FROM {table} WHERE frequency = 'semiannual'")
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="check")
            batch.create_check_constraint(constraint, _in(OLD_FREQUENCIES))
