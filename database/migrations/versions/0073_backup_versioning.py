"""backup_jobs.app_version + schema_revision: restore-compatibility labels

Every backup records the app version and alembic revision it was taken
under (also sealed inside the encrypted archive as manifest.json). The
restore UI shows the label; the restore path refuses archives from a
newer app than the box is running.

Revision ID: 0073_backup_versioning
Revises: 0072_answer_ms
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073_backup_versioning"
down_revision: str | None = "0072_answer_ms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backup_jobs", sa.Column("app_version", sa.String(50), nullable=True))
    op.add_column("backup_jobs", sa.Column("schema_revision", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("backup_jobs", "schema_revision")
    op.drop_column("backup_jobs", "app_version")
