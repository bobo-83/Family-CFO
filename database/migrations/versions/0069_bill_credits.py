"""bill_credits: statement credits tracked against a bill

Net-metered utility bills spend months in credit (solar export exceeds
usage). The bill's amount stays the positive recurring obligation; each
scanned statement's credit is recorded here so the Bills page can show
which bills carry credits and roll them up per month and per year.

Revision ID: 0069_bill_credits
Revises: 0068_semiannual_frequency
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0069_bill_credits"
down_revision: str | None = "0068_semiannual_frequency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bill_credits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("bill_id", sa.String(36), sa.ForeignKey("bills.id"), nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("statement_date", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_bill_credits_amount_positive"),
    )


def downgrade() -> None:
    op.drop_table("bill_credits")
