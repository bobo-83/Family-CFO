"""Retire QR bootstrap without touching established device sessions (ADR 0079).

Revision ID: 0097_retire_qr_pairing
Revises: 0096_backup_repair_index
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0097_retire_qr_pairing"
down_revision: str | None = "0096_backup_repair_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No device/session/wrap references this table. All outstanding bearer codes
    # disappear, while already-issued credentials continue to authenticate.
    op.drop_table("pairing_sessions")


def downgrade() -> None:
    # Schema-only rollback: never resurrect old QR bearer secrets.
    op.create_table(
        "pairing_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("qr_payload", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
