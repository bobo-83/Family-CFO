"""document_extractions

Revision ID: 0022_document_extractions
Revises: 0021_documents
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_document_extractions"
down_revision: str | None = "0021_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOCUMENT_EXTRACTION_TYPES = ("pdf_text", "ocr")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "document_extractions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False, unique=True
        ),
        sa.Column("extraction_type", sa.String(20), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("structured_fields_json", sa.JSON, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("warnings_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"extraction_type in {_sql_in(DOCUMENT_EXTRACTION_TYPES)}", name="ck_document_extractions_type"
        ),
        sa.CheckConstraint(
            "confidence >= 0 and confidence <= 1", name="ck_document_extractions_confidence_range"
        ),
    )


def downgrade() -> None:
    op.drop_table("document_extractions")
