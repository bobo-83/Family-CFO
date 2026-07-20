"""member 👍/👎 feedback on advisor answers

ADR 0044: a member rates an advisor answer; the idle study job later reviews the
flagged ones and distills a lesson into household knowledge, then marks them
reviewed. One row per (recommendation, member).

Revision ID: 0063_advisor_feedback
Revises: 0062_debt_rate_fraction
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_advisor_feedback"
down_revision: str | None = "0062_debt_rate_fraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "advisor_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False
        ),
        sa.Column(
            "recommendation_id",
            sa.String(36),
            sa.ForeignKey("recommendations.id"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("rating", sa.String(10), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_advisor_feedback_recommendation_user",
        "advisor_feedback",
        ["recommendation_id", "created_by_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_advisor_feedback_recommendation_user", table_name="advisor_feedback")
    op.drop_table("advisor_feedback")
