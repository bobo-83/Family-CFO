"""compensation profiles per earner (M73)

Revision ID: 0043_income_profiles
Revises: 0042_household_state
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043_income_profiles"
down_revision: str | None = "0042_household_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Declared compensation beats deposit inference for RSU-heavy earners:
    # base salary + RSU value/cadence + bonus, with optional last-year W2
    # actuals for calibration.
    op.create_table(
        "income_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("base_salary_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("rsu_annual_minor", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("rsu_frequency", sa.String(20), nullable=True),
        sa.Column("rsu_next_vest_date", sa.Date, nullable=True),
        sa.Column("bonus_percent", sa.Float, nullable=False, server_default="0"),
        sa.Column("bonus_month", sa.Integer, nullable=True),
        sa.Column("w2_year", sa.Integer, nullable=True),
        sa.Column("w2_wages_minor", sa.BigInteger, nullable=True),
        sa.Column("w2_withheld_minor", sa.BigInteger, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("income_profiles")
