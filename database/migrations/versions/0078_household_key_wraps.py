"""household_key_wraps: member, device, and recovery wraps of the DEK

ADR 0072 Phase 2. The household data key gains unwrap paths beyond the box
master key: per member (KEK derived from their password with its own salt),
per paired device (ECIES against the pairing P-256 public key), and a
one-time-displayed recovery key. Dormant redundancy in convenient mode;
sealed mode (Phase 3) makes them the only paths.

Revision ID: 0078_household_key_wraps
Revises: 0077_widen_sealed_columns
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078_household_key_wraps"
down_revision: str | None = "0077_widen_sealed_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "household_key_wraps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=True),
        sa.Column("wrap_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind in ('member', 'device', 'recovery')", name="ck_household_key_wraps_kind"
        ),
    )
    op.create_index("ix_household_key_wraps_household", "household_key_wraps", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_household_key_wraps_household", table_name="household_key_wraps")
    op.drop_table("household_key_wraps")
