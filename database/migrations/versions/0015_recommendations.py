"""recommendations

Revision ID: 0015_recommendations
Revises: 0014_financial_calculations
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_recommendations"
down_revision: str | None = "0014_financial_calculations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPLANATION_SOURCES = ("deterministic_stub",)


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "recommendations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("scenario_id", sa.String(36), sa.ForeignKey("scenarios.id"), nullable=True),
        sa.Column("answer", sa.Text, nullable=False),
        sa.Column("assumptions_json", sa.JSON, nullable=False),
        sa.Column("impacts_json", sa.JSON, nullable=False),
        sa.Column("tradeoffs_json", sa.JSON, nullable=False),
        sa.Column("alternatives_json", sa.JSON, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("calculation_refs_json", sa.JSON, nullable=False),
        sa.Column("warnings_json", sa.JSON, nullable=False),
        sa.Column("explanation_source", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"explanation_source in {_sql_in(EXPLANATION_SOURCES)}",
            name="ck_recommendations_explanation_source",
        ),
        sa.CheckConstraint(
            "confidence >= 0 and confidence <= 1", name="ck_recommendations_confidence_range"
        ),
    )


def downgrade() -> None:
    op.drop_table("recommendations")
