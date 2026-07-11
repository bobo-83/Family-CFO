"""income analysis: per-transaction overrides + household tax settings (M61)

Revision ID: 0041_income_analysis
Revises: 0040_bill_suggestion_dismissals
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_income_analysis"
down_revision: str | None = "0040_bill_suggestion_dismissals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # User verdicts on individual inflow transactions: "exclude" removes a
    # detected deposit from income, "include" adds one the scan missed.
    op.create_table(
        "income_transaction_overrides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column(
            "transaction_id", sa.String(36), sa.ForeignKey("transactions.id"), nullable=False
        ),
        sa.Column("verdict", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_income_overrides_household_transaction",
        "income_transaction_overrides",
        ["household_id", "transaction_id"],
        unique=True,
    )
    # Tax-estimate settings (null = defaults: married_joint, deposits are net).
    op.add_column("households", sa.Column("tax_filing_status", sa.String(20), nullable=True))
    op.add_column("households", sa.Column("income_treated_as_net", sa.Boolean, nullable=True))


def downgrade() -> None:
    op.drop_column("households", "income_treated_as_net")
    op.drop_column("households", "tax_filing_status")
    op.drop_index(
        "uq_income_overrides_household_transaction", table_name="income_transaction_overrides"
    )
    op.drop_table("income_transaction_overrides")
