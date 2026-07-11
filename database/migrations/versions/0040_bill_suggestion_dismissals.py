"""bill suggestion dismissals (M58)

Revision ID: 0040_bill_suggestion_dismissals
Revises: 0039_household_memories
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_bill_suggestion_dismissals"
down_revision: str | None = "0039_household_memories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bill_suggestion_dismissals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("merchant_key", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_bill_suggestion_dismissals_household_key",
        "bill_suggestion_dismissals",
        ["household_id", "merchant_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_bill_suggestion_dismissals_household_key",
        table_name="bill_suggestion_dismissals",
    )
    op.drop_table("bill_suggestion_dismissals")
