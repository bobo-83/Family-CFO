"""Add durable backup deletion intents (issue #116, ADR 0077).

Revision ID: 0094_backup_delete_intents
Revises: 0093_box_global_backup_settings
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0094_backup_delete_intents"
down_revision: str | None = "0093_box_global_backup_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ACTIONS = (
    "'pruned', 'explicit_deleted', 'reconciled', 'prune_failed', "
    "'inventory_failed', 'inventory_succeeded', 'capacity_blocked', "
    "'lock_skipped', 'anomaly_detected', 'restore_reset'"
)
_NEW_ACTIONS = f"'delete_pending', {_OLD_ACTIONS}"


def _replace_action_constraint(actions: str) -> None:
    with op.batch_alter_table("backup_retention_events") as batch:
        batch.drop_constraint("ck_backup_retention_events_action", type_="check")
        batch.create_check_constraint(
            "ck_backup_retention_events_action", f"action IN ({actions})"
        )


def upgrade() -> None:
    _replace_action_constraint(_NEW_ACTIONS)


def downgrade() -> None:
    # Older code cannot name an intent, but retaining it as a failed prune keeps
    # the causality record rather than dropping operational evidence.
    op.execute(
        "UPDATE backup_retention_events SET action = 'prune_failed' "
        "WHERE action = 'delete_pending'"
    )
    _replace_action_constraint(_OLD_ACTIONS)
