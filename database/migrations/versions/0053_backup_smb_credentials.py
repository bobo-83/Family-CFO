"""SMB (Synology) backup destination credentials (M98)

Instead of asking the user to mount a CIFS share on the box, store the Synology
SMB connection details so the app uploads backups over SMB directly (userspace,
no host mount). The password is Fernet-encrypted at rest and never returned.

Additive and nullable; a household with no SMB host set stays on-box only.

Revision ID: 0053_backup_smb_credentials
Revises: 0052_backup_destination
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_backup_smb_credentials"
down_revision: str | None = "0052_backup_destination"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("households", sa.Column("backup_smb_host", sa.String(255), nullable=True))
    op.add_column("households", sa.Column("backup_smb_share", sa.String(255), nullable=True))
    op.add_column("households", sa.Column("backup_smb_folder", sa.String(500), nullable=True))
    op.add_column("households", sa.Column("backup_smb_username", sa.String(255), nullable=True))
    op.add_column(
        "households", sa.Column("backup_smb_password_encrypted", sa.Text(), nullable=True)
    )
    op.add_column("households", sa.Column("backup_smb_domain", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "backup_smb_domain")
    op.drop_column("households", "backup_smb_password_encrypted")
    op.drop_column("households", "backup_smb_username")
    op.drop_column("households", "backup_smb_folder")
    op.drop_column("households", "backup_smb_share")
    op.drop_column("households", "backup_smb_host")
