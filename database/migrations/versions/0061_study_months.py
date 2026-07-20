"""study coverage of the transaction history

ADR 0040: the worker studies one complete calendar month at a time while the
box is idle, distilling Postgres history into advisor memories. One row per
household x month studied; digest_hash fingerprints the month's data so later
edits mark it stale for re-study.

Revision ID: 0061_study_months
Revises: 0060_roles_and_rights
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_study_months"
down_revision: str | None = "0060_roles_and_rights"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "study_months",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False
        ),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("digest_hash", sa.String(64), nullable=False),
        sa.Column("insight_count", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("studied_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_study_months_household_month",
        "study_months",
        ["household_id", "month"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_study_months_household_month", table_name="study_months")
    op.drop_table("study_months")
