"""goals

Revision ID: 0012_goals
Revises: 0011_income_sources
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_goals"
down_revision: str | None = "0011_income_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GOAL_TYPES = ("emergency_fund", "vacation", "retirement", "college", "vehicle", "renovation", "other")


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("target_minor", sa.BigInteger, nullable=False),
        sa.Column("current_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("target_date", sa.Date, nullable=True),
        sa.Column("priority", sa.Integer, nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"type in {GOAL_TYPES!r}", name="ck_goals_type"),
        sa.CheckConstraint("priority between 1 and 5", name="ck_goals_priority"),
    )


def downgrade() -> None:
    op.drop_table("goals")
