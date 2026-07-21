"""copy-link household invites

ADR 0056: an admin invites a new member by email and shares a one-time LINK
(the box sends no email). The invitee sets their own password on the join page.
The link's CSPRNG token is stored SHA-256-hashed; status (pending / accepted /
expired / revoked) is computed at read time from the timestamps.

Revision ID: 0064_household_invites
Revises: 0063_advisor_feedback
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064_household_invites"
down_revision: str | None = "0063_advisor_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "household_invites",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("role_id", sa.String(36), sa.ForeignKey("roles.id"), nullable=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "invited_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "accepted_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_household_invites_household_id", "household_invites", ["household_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_household_invites_household_id", table_name="household_invites")
    op.drop_table("household_invites")
