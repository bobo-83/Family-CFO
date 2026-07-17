"""allow the 401k_loan account type (M96)

A 401(k) loan is a real recurring obligation (monthly repayment) but is owed to
your own retirement, so it is tracked as its own account type — net-worth-neutral
and excluded from external debt. The accounts table's CHECK constraint allowlists
account types, so it has to learn the new one.

Additive: no data is rewritten, existing types remain valid.

Revision ID: 0047_account_type_401k_loan
Revises: 0046_credit_cards_paid_in_full
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0047_account_type_401k_loan"
down_revision: str | None = "0046_credit_cards_paid_in_full"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_TYPES = (
    "checking", "savings", "credit_card", "brokerage", "retirement", "hsa", "529",
    "mortgage", "auto_loan", "student_loan", "real_estate", "other_asset", "other_liability",
)
NEW_TYPES = (
    "checking", "savings", "credit_card", "brokerage", "retirement", "hsa", "529",
    "mortgage", "auto_loan", "student_loan", "401k_loan", "real_estate", "other_asset",
    "other_liability",
)


def _in(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"type in ({joined})"


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_constraint("ck_accounts_type", type_="check")
        batch.create_check_constraint("ck_accounts_type", _in(NEW_TYPES))


def downgrade() -> None:
    op.execute("DELETE FROM accounts WHERE type = '401k_loan'")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_constraint("ck_accounts_type", type_="check")
        batch.create_check_constraint("ck_accounts_type", _in(OLD_TYPES))
