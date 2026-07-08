"""import_files

Revision ID: 0020_import_files
Revises: 0019_imports
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_import_files"
down_revision: str | None = "0019_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("import_id", sa.String(36), sa.ForeignKey("imports.id"), nullable=False, unique=True),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("import_files")
