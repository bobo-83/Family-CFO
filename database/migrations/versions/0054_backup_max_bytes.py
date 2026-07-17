"""backup total-size cap (M98)

Beyond the count-based retention, let the owner cap the combined size of all
backups (on-box and on the Synology). When a new backup pushes the total over the
cap, the oldest backups are deleted first until it's back under.

Additive and nullable; NULL = no size cap (count retention still applies).

Revision ID: 0054_backup_max_bytes
Revises: 0053_backup_smb_credentials
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_backup_max_bytes"
down_revision: str | None = "0053_backup_smb_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("households", sa.Column("backup_max_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "backup_max_bytes")
