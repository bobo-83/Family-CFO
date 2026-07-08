"""imports

Revision ID: 0019_imports
Revises: 0018_pairing_and_devices
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_imports"
down_revision: str | None = "0018_pairing_and_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMPORT_SOURCE_TYPES = ("csv", "pdf", "ofx", "qfx")
IMPORT_STATUSES = ("pending", "processing", "needs_review", "completed", "discarded", "failed")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("skipped_row_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"source_type in {_sql_in(IMPORT_SOURCE_TYPES)}", name="ck_imports_source_type"),
        sa.CheckConstraint(f"status in {_sql_in(IMPORT_STATUSES)}", name="ck_imports_status"),
    )


def downgrade() -> None:
    op.drop_table("imports")
