"""savings_contributions + dismissals: declared or confirmed saving (#203)

Detection alone cannot see a contribution whose destination account never
syncs — the common case for 529s and retirement plans, and the reason two
detector iterations (#201, #207) can come back empty for a household that
is plainly saving. The user declares it once; the app tracks it from then on.

Revision ID: 0082_savings_contributions
Revises: 0081_liability_balance_sign
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0082_savings_contributions"
down_revision: str | None = "0081_liability_balance_sign"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FREQUENCIES = (
    "weekly", "biweekly", "semimonthly", "monthly", "quarterly", "semiannual", "annual",
)


def upgrade() -> None:
    frequencies = ", ".join(f"'{f}'" for f in _FREQUENCIES)
    op.create_table(
        "savings_contributions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column(
            "source_account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column(
            "destination_account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("label_key", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_savings_contributions_amount_positive"),
        sa.CheckConstraint(
            f"frequency in ({frequencies})", name="ck_savings_contributions_frequency"
        ),
    )
    op.create_table(
        "savings_contribution_dismissals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column(
            "source_account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column(
            "destination_account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_savings_dismissals_household_route",
        "savings_contribution_dismissals",
        ["household_id", "source_account_id", "destination_account_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_savings_dismissals_household_route", table_name="savings_contribution_dismissals"
    )
    op.drop_table("savings_contribution_dismissals")
    op.drop_table("savings_contributions")
