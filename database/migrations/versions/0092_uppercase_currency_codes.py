"""Canonical upper-case currency codes on every stored row (#152 review)

`POST /accounts` accepted and stored a lower-case ISO code while the engine's
`Money` canonicalises to upper case, so a "usd" account in a USD household
compared unequal to its own base currency and — once totals began excluding
out-of-base balances instead of crashing on them — was left out of its own
household's figures, with a warning that it was "held in USD". Ingress now
normalises every code; this brings the rows that predate it in line.

Idempotent. The downgrade is a no-op: there is no original case to restore,
and none was ever meant.

Revision ID: 0092_uppercase_currency_codes
Revises: 0091_household_key_generation
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0092_uppercase_currency_codes"
down_revision: str | None = "0091_household_key_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every column a base-currency comparison reads. Transaction rows are left as
# they are: their currency is read through the same comparisons only after an
# account's, and a sealed household's transaction amounts are encrypted while
# its currency codes are not — a mass rewrite there is a separate decision.
_CURRENCY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("households", "base_currency"),
    ("accounts", "currency"),
    ("goals", "currency"),
    ("bills", "currency"),
    ("income_sources", "currency"),
    ("budgets", "currency"),
)


def upgrade() -> None:
    for table, column in _CURRENCY_COLUMNS:
        op.execute(
            sa.text(f"UPDATE {table} SET {column} = UPPER({column}) WHERE {column} <> UPPER({column})")
        )


def downgrade() -> None:
    pass
