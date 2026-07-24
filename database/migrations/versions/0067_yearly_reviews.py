"""cached yearly review narratives

M-yearly: the Overview's year view shows a grounded AI narrative of the year
(what happened, what could improve). Generation costs a model round, so the
result is cached per household+year and regenerated on demand or when a new
month completes.

Revision ID: 0067_yearly_reviews
Revises: 0066_system_admins
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_yearly_reviews"
down_revision: str | None = "0066_system_admins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "yearly_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False
        ),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("suggestions_json", sa.JSON, nullable=False),
        sa.Column("months_covered", sa.Integer, nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "year", name="uq_yearly_reviews_household_year"),
    )


def downgrade() -> None:
    op.drop_table("yearly_reviews")
