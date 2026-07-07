"""household_memberships

Revision ID: 0004_household_memberships
Revises: 0003_users
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_household_memberships"
down_revision: str | None = "0003_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOUSEHOLD_ROLES = ("owner", "adult", "viewer", "child")


def upgrade() -> None:
    op.create_table(
        "household_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"role in {HOUSEHOLD_ROLES!r}", name="ck_household_memberships_role"),
        sa.UniqueConstraint("household_id", "user_id", name="uq_household_memberships_household_user"),
    )


def downgrade() -> None:
    op.drop_table("household_memberships")
