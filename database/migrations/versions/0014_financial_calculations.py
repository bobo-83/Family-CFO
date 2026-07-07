"""financial_calculations

Revision ID: 0014_financial_calculations
Revises: 0013_scenarios
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_financial_calculations"
down_revision: str | None = "0013_scenarios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CALCULATION_TYPES = (
    "net_worth",
    "cash_flow",
    "budget_summary",
    "emergency_fund",
    "goal_progress",
    "purchase_impact",
)


def upgrade() -> None:
    op.create_table(
        "financial_calculations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("calculation_type", sa.String(30), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("inputs_json", sa.JSON, nullable=False),
        sa.Column("assumptions_json", sa.JSON, nullable=False),
        sa.Column("warnings_json", sa.JSON, nullable=False),
        sa.Column("outputs_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"calculation_type in {CALCULATION_TYPES!r}", name="ck_financial_calculations_type"
        ),
    )


def downgrade() -> None:
    op.drop_table("financial_calculations")
