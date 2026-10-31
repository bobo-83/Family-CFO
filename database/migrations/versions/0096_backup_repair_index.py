"""Index archive-scoped retention repair lookups (PR #163, ADR 0077).

Revision ID: 0096_backup_repair_index
Revises: 0095_backup_settings_revision
Create Date: 2026-10-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0096_backup_repair_index"
down_revision: str | None = "0095_backup_settings_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_backup_retention_events_repair_lookup",
        "backup_retention_events",
        ["destination_generation", "archive_key", "action", "occurred_at", "event_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_backup_retention_events_repair_lookup",
        table_name="backup_retention_events",
    )
