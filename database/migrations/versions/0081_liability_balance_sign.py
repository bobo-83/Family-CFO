"""Correct positive liability balances to negative (#194)

Some institutions report a loan/credit balance as a plain positive number
(the outstanding amount, unsigned). Stored verbatim, that liability was
counted as an ASSET in net worth. Owed money is always negative in this
app's model, so flip any positive balance on a liability-typed account.
The sync path now normalizes new balances the same way.

Idempotent: a negative balance is left alone, so re-running is a no-op.

Revision ID: 0081_liability_balance_sign
Revises: 0080_seal_amounts
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0081_liability_balance_sign"
down_revision: str | None = "0080_seal_amounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIABILITY_TYPES = (
    "credit_card", "mortgage", "auto_loan", "student_loan", "other_liability", "401k_loan",
)


def upgrade() -> None:
    placeholders = ", ".join(f"'{t}'" for t in _LIABILITY_TYPES)
    op.execute(
        text(
            "UPDATE account_balances SET balance_minor = -balance_minor "
            "WHERE balance_minor > 0 AND account_id IN ("
            f"  SELECT id FROM accounts WHERE type IN ({placeholders})"
            ")"
        )
    )


def downgrade() -> None:
    # Not reversible without knowing which rows were flipped; the corrected
    # sign is the right one, so downgrade is a deliberate no-op.
    pass
