"""transactions.import_id

Revision ID: 0023_transactions_import_id
Revises: 0022_document_extractions
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_transactions_import_id"
down_revision: str | None = "0022_document_extractions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "import_id",
                sa.String(36),
                sa.ForeignKey("imports.id", name="fk_transactions_import_id"),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column("possible_duplicate", sa.Boolean, nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_column("possible_duplicate")
        batch_op.drop_column("import_id")
