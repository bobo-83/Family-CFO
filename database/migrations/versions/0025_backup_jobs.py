"""backup_jobs

Revision ID: 0025_backup_jobs
Revises: 0024_reports
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_backup_jobs"
down_revision: str | None = "0024_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKUP_JOB_STATUSES = ("pending", "running", "completed", "failed")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "backup_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("storage_path", sa.String(500), nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pruned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status in {_sql_in(BACKUP_JOB_STATUSES)}", name="ck_backup_jobs_status"),
    )


def downgrade() -> None:
    op.drop_table("backup_jobs")
