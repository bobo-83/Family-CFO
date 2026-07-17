"""account maturity_date for loans/leases (M96)

A loan or lease has an end date; storing it lets the app show the maturity date
and how many payments/months are left, and (for a lease) derive the remaining
obligation. Nullable — only loans/leases set it.

Additive: no data is rewritten.

Revision ID: 0048_account_maturity_date
Revises: 0047_account_type_401k_loan
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_account_maturity_date"
down_revision: str | None = "0047_account_type_401k_loan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("maturity_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "maturity_date")
