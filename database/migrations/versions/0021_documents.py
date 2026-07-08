"""documents

Revision ID: 0021_documents
Revises: 0020_import_files
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_documents"
down_revision: str | None = "0020_import_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("import_id", sa.String(36), sa.ForeignKey("imports.id"), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("documents")
