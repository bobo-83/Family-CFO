"""configurable off-box backup destination + schedule (M98)

Encrypted daily backups already run, but only into an on-box volume — a single
point of failure. This lets the owner point backups at a mounted network share
(e.g. a Synology over SMB) and choose the cadence, and records whether each
backup reached that share so the app can warn on failure.

- households.backup_destination_path: where to copy each .enc (NULL = on-box only)
- households.backup_frequency: 'daily' (default) | 'weekly' | 'off'
- backup_jobs.remote_status: 'synced' | 'failed' | 'skipped' (NULL = not attempted)
- backup_jobs.remote_error: the reason a copy to the share failed

Additive and nullable; existing rows keep local-only daily backups.

Revision ID: 0052_backup_destination
Revises: 0051_account_institution
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_backup_destination"
down_revision: str | None = "0051_account_institution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("households", sa.Column("backup_destination_path", sa.String(500), nullable=True))
    op.add_column(
        "households",
        sa.Column("backup_frequency", sa.String(20), nullable=False, server_default="daily"),
    )
    op.add_column("backup_jobs", sa.Column("remote_status", sa.String(20), nullable=True))
    op.add_column("backup_jobs", sa.Column("remote_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("backup_jobs", "remote_error")
    op.drop_column("backup_jobs", "remote_status")
    op.drop_column("households", "backup_frequency")
    op.drop_column("households", "backup_destination_path")
