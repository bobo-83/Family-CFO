"""store debt interest rate as a decimal fraction, not a percentage

ADR 0042: the engine, the advisor tools, and annual_return_rate all treat rates
as decimal fractions (0.06 = 6%), but both clients stored the raw typed/scanned
percentage into accounts.annual_interest_rate (9.5 for 9.5%). The engine then
read 9.5 as 950% APR, so every payment looked smaller than the accruing
interest and no debt could be modeled ("interest-only").

Any stored rate > 1.0 is a mis-stored percentage (no real APR is >= 100% as a
fraction); divide it by 100. Values <= 1.0 are already fractions (fixtures,
newly-fixed clients) and are left alone. A genuine sub-1% APR entered as a
percentage (e.g. "0.5") can't be distinguished and is left as-is — rare, and
flagged in ADR 0042.

Revision ID: 0062_debt_rate_fraction
Revises: 0061_study_months
Create Date: 2026-07-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0062_debt_rate_fraction"
down_revision: str | None = "0061_study_months"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE accounts SET annual_interest_rate = annual_interest_rate / 100.0 "
        "WHERE annual_interest_rate IS NOT NULL AND annual_interest_rate > 1.0"
    )


def downgrade() -> None:
    # Best-effort inverse: re-inflate the fractions this migration deflated. Rows
    # already below 1.0 before the upgrade are indistinguishable now, so this is
    # not a perfect round-trip — acceptable for a units correction.
    op.execute(
        "UPDATE accounts SET annual_interest_rate = annual_interest_rate * 100.0 "
        "WHERE annual_interest_rate IS NOT NULL AND annual_interest_rate <= 1.0"
    )
