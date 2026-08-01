"""rsu_grants, rsu_vest_events, stock_quotes: grant-based RSU tracking

The flat "RSU dollars per year" model can't answer how many shares vest
when, or what they're worth today. Each grant (units, grant date, cycle,
cadence) derives an editable vest schedule, and a cached live quote per
ticker values it — replacing the flat figure wherever the quote exists.

Revision ID: 0070_rsu_grants
Revises: 0069_bill_credits
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070_rsu_grants"
down_revision: str | None = "0069_bill_credits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rsu_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column(
            "income_profile_id",
            sa.String(36),
            sa.ForeignKey("income_profiles.id"),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(8), nullable=False),
        sa.Column("units", sa.BigInteger, nullable=False),
        sa.Column("grant_date", sa.Date, nullable=False),
        sa.Column("vest_years", sa.Integer, nullable=False, server_default="2"),
        sa.Column("frequency", sa.String(20), nullable=False, server_default="quarterly"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("units > 0", name="ck_rsu_grants_units_positive"),
    )
    op.create_table(
        "rsu_vest_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("grant_id", sa.String(36), sa.ForeignKey("rsu_grants.id"), nullable=False),
        sa.Column("vest_date", sa.Date, nullable=False),
        sa.Column("units", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("units > 0", name="ck_rsu_vest_events_units_positive"),
    )
    op.create_table(
        "stock_quotes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("ticker", sa.String(8), nullable=False),
        sa.Column("price_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
    )
    op.create_index(
        "uq_stock_quotes_household_ticker",
        "stock_quotes",
        ["household_id", "ticker"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_stock_quotes_household_ticker", table_name="stock_quotes")
    op.drop_table("stock_quotes")
    op.drop_table("rsu_vest_events")
    op.drop_table("rsu_grants")
