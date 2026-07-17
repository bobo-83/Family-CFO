"""audit undo

Adds the columns the Activity/History screen (M101) needs to reverse an action:
``undo_token`` carries the JSON needed to undo (e.g. a transaction's prior
category), and ``reverted_at`` records that it has already been undone so the UI
disables the button. Both are null for the many actions that aren't reversible.

Revision ID: 0056_audit_undo
Revises: 0055_txn_note_attachment
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056_audit_undo"
down_revision: str | None = "0055_txn_note_attachment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("undo_token", sa.Text(), nullable=True))
    op.add_column(
        "audit_events",
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_events", "reverted_at")
    op.drop_column("audit_events", "undo_token")
