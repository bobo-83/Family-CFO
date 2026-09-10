"""Box-global backup settings and retention journal (issue #116, ADR 0077).

The migration creates schema only.  The first repository read bootstraps the
singleton from database evidence and the compatibility settings supplied by the
running process; Alembic must not make durable policy depend on its environment.

Revision ID: 0093_box_global_backup_settings
Revises: 0092_uppercase_currency_codes
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0093_box_global_backup_settings"
down_revision: str | None = "0092_uppercase_currency_codes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FREQUENCIES = "'off', 'every_15min', 'hourly', 'every_6h', 'daily', 'weekly'"
_EVENT_ACTIONS = (
    "'pruned', 'explicit_deleted', 'reconciled', 'prune_failed', "
    "'inventory_failed', 'inventory_succeeded', 'capacity_blocked', "
    "'lock_skipped', 'anomaly_detected', 'restore_reset'"
)


def _policy_check(prefix: str) -> sa.CheckConstraint:
    mode = f"{prefix}_retention_mode"
    keep_all = f"{prefix}_keep_all_days"
    daily = f"{prefix}_daily_until_days"
    weekly = f"{prefix}_weekly_until_days"
    return sa.CheckConstraint(
        f"(({mode} = 'keep_all' AND {keep_all} IS NULL AND {daily} IS NULL "
        f"AND {weekly} IS NULL) OR ({mode} = 'tiered' AND {keep_all} IS NOT NULL "
        f"AND {daily} IS NOT NULL AND {weekly} IS NOT NULL AND {keep_all} >= 1 "
        f"AND {keep_all} <= {daily} AND {daily} <= {weekly} AND {weekly} <= 3650))",
        name=f"ck_backup_settings_{prefix}_policy",
    )


def upgrade() -> None:
    op.create_table(
        "backup_settings",
        sa.Column("key", sa.String(length=16), nullable=False),
        sa.Column("frequency", sa.String(length=20), nullable=False),
        sa.Column("smb_host", sa.String(length=255), nullable=True),
        sa.Column("smb_share", sa.String(length=255), nullable=True),
        sa.Column("smb_folder", sa.String(length=500), nullable=True),
        sa.Column("smb_username", sa.String(length=255), nullable=True),
        sa.Column("smb_password_encrypted", sa.Text(), nullable=True),
        sa.Column("smb_domain", sa.String(length=120), nullable=True),
        sa.Column("local_retention_mode", sa.String(length=16), nullable=False),
        sa.Column("local_keep_all_days", sa.Integer(), nullable=True),
        sa.Column("local_daily_until_days", sa.Integer(), nullable=True),
        sa.Column("local_weekly_until_days", sa.Integer(), nullable=True),
        sa.Column("offbox_retention_mode", sa.String(length=16), nullable=False),
        sa.Column("offbox_keep_all_days", sa.Integer(), nullable=True),
        sa.Column("offbox_daily_until_days", sa.Integer(), nullable=True),
        sa.Column("offbox_weekly_until_days", sa.Integer(), nullable=True),
        sa.Column("local_max_bytes", sa.BigInteger(), nullable=True),
        sa.Column("offbox_max_bytes", sa.BigInteger(), nullable=True),
        sa.Column("local_min_free_bytes", sa.BigInteger(), nullable=False),
        sa.Column("offbox_min_free_bytes", sa.BigInteger(), nullable=False),
        sa.Column("legacy_conflict_detected", sa.Boolean(), nullable=False),
        sa.Column("retention_review_required", sa.Boolean(), nullable=False),
        sa.Column("retention_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("local_destination_generation", sa.String(length=36), nullable=False),
        sa.Column("offbox_destination_generation", sa.String(length=36), nullable=False),
        sa.Column("local_path_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("key = 'global'", name="ck_backup_settings_global_key"),
        sa.CheckConstraint(
            f"frequency IN ({_FREQUENCIES})", name="ck_backup_settings_frequency"
        ),
        sa.CheckConstraint(
            "local_retention_mode IN ('tiered', 'keep_all')",
            name="ck_backup_settings_local_mode",
        ),
        sa.CheckConstraint(
            "offbox_retention_mode IN ('tiered', 'keep_all')",
            name="ck_backup_settings_offbox_mode",
        ),
        _policy_check("local"),
        _policy_check("offbox"),
        sa.CheckConstraint(
            "local_max_bytes IS NULL OR local_max_bytes > 0",
            name="ck_backup_settings_local_max_bytes",
        ),
        sa.CheckConstraint(
            "offbox_max_bytes IS NULL OR offbox_max_bytes > 0",
            name="ck_backup_settings_offbox_max_bytes",
        ),
        sa.CheckConstraint(
            "local_min_free_bytes >= 0", name="ck_backup_settings_local_min_free_bytes"
        ),
        sa.CheckConstraint(
            "offbox_min_free_bytes >= 0", name="ck_backup_settings_offbox_min_free_bytes"
        ),
        sa.PrimaryKeyConstraint("key"),
    )

    with op.batch_alter_table("backup_jobs") as batch:
        batch.add_column(sa.Column("prune_reason", sa.String(length=32), nullable=True))

    op.create_table(
        "backup_retention_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("destination", sa.String(length=16), nullable=False),
        sa.Column("archive_key", sa.String(length=500), nullable=True),
        sa.Column("backup_job_id", sa.String(length=36), nullable=True),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("event_key", sa.String(length=64), nullable=False),
        sa.Column("destination_generation", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("archive_taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timestamp_source", sa.String(length=32), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("policy_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_snapshot", sa.JSON(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "destination IN ('local', 'offbox')",
            name="ck_backup_retention_events_destination",
        ),
        sa.CheckConstraint(
            f"action IN ({_EVENT_ACTIONS})", name="ck_backup_retention_events_action"
        ),
        sa.CheckConstraint(
            "timestamp_source IS NULL OR timestamp_source IN "
            "('job_started_at', 'remote_modified_at')",
            name="ck_backup_retention_events_timestamp_source",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name="ck_backup_retention_events_size_bytes",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_backup_retention_events_event_key"),
    )
    op.create_index(
        "ix_backup_retention_events_generation_occurred",
        "backup_retention_events",
        ["destination_generation", "occurred_at"],
    )


def downgrade() -> None:
    # Old releases have one household-scoped shape.  Preserve the representable
    # destination/cadence fields on every household; unequal caps cannot be
    # represented truthfully and therefore become NULL.  Tier policies have no
    # legacy representation and are intentionally not fabricated here.
    bind = op.get_bind()
    settings = sa.table(
        "backup_settings",
        sa.column("key", sa.String()),
        sa.column("frequency", sa.String()),
        sa.column("smb_host", sa.String()),
        sa.column("smb_share", sa.String()),
        sa.column("smb_folder", sa.String()),
        sa.column("smb_username", sa.String()),
        sa.column("smb_password_encrypted", sa.Text()),
        sa.column("smb_domain", sa.String()),
        sa.column("local_max_bytes", sa.BigInteger()),
        sa.column("offbox_max_bytes", sa.BigInteger()),
    )
    row = bind.execute(sa.select(settings).where(settings.c.key == "global")).mappings().first()
    if row is not None:
        households = sa.table(
            "households",
            sa.column("backup_frequency", sa.String()),
            sa.column("backup_smb_host", sa.String()),
            sa.column("backup_smb_share", sa.String()),
            sa.column("backup_smb_folder", sa.String()),
            sa.column("backup_smb_username", sa.String()),
            sa.column("backup_smb_password_encrypted", sa.Text()),
            sa.column("backup_smb_domain", sa.String()),
            sa.column("backup_max_bytes", sa.BigInteger()),
        )
        shared_cap = (
            row["local_max_bytes"]
            if row["local_max_bytes"] == row["offbox_max_bytes"]
            else None
        )
        bind.execute(
            sa.update(households).values(
                backup_frequency=row["frequency"],
                backup_smb_host=row["smb_host"],
                backup_smb_share=row["smb_share"],
                backup_smb_folder=row["smb_folder"],
                backup_smb_username=row["smb_username"],
                backup_smb_password_encrypted=row["smb_password_encrypted"],
                backup_smb_domain=row["smb_domain"],
                backup_max_bytes=shared_cap,
            )
        )

    op.drop_index(
        "ix_backup_retention_events_generation_occurred",
        table_name="backup_retention_events",
    )
    op.drop_table("backup_retention_events")
    with op.batch_alter_table("backup_jobs") as batch:
        batch.drop_column("prune_reason")
    op.drop_table("backup_settings")
