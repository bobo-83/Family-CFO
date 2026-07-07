"""income_sources

Revision ID: 0011_income_sources
Revises: 0010_bills
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_income_sources"
down_revision: str | None = "0010_bills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RECURRING_FREQUENCIES = ("weekly", "biweekly", "semimonthly", "monthly", "quarterly", "annual")


def upgrade() -> None:
    op.create_table(
        "income_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("frequency", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"frequency in {RECURRING_FREQUENCIES!r}", name="ck_income_sources_frequency"
        ),
    )


def downgrade() -> None:
    op.drop_table("income_sources")
