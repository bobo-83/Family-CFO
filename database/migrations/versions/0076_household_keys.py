"""household_keys: per-household data keys, wrapped by the box master key

ADR 0072 Phase 1: content columns (chats, advisor answers, memories,
feedback notes, document text) are encrypted under per-household keys, so
database dumps, stolen disks, and whole-box backups stop being skeleton
keys for household content. Keys are created lazily per household; the
master key lives in the environment, never in the database.

Revision ID: 0076_household_keys
Revises: 0075_bill_payment_links
Create Date: 2026-08-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076_household_keys"
down_revision: str | None = "0075_bill_payment_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "household_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id",
            sa.String(36),
            sa.ForeignKey("households.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("wrapped_dek", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("household_keys")
