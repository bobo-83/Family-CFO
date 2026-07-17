"""per-transaction note + image attachment (M100)

Opaque lines like "Check #665" tell you nothing later. Let the user add a free-text
note and attach a photo (e.g. a scan of the check) so they remember what it was for.
The attachment is stored on disk under the import-staging dir (so it rides along in
backups); the row keeps its path + content type.

Additive and nullable.

Revision ID: 0055_txn_note_attachment
Revises: 0054_backup_max_bytes
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_txn_note_attachment"
down_revision: str | None = "0054_backup_max_bytes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("note", sa.Text(), nullable=True))
    op.add_column("transactions", sa.Column("attachment_path", sa.String(500), nullable=True))
    op.add_column(
        "transactions", sa.Column("attachment_content_type", sa.String(100), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("transactions", "attachment_content_type")
    op.drop_column("transactions", "attachment_path")
    op.drop_column("transactions", "note")
