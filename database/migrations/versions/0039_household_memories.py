"""household memory + conversation summaries (M57, ADR 0016)

Revision ID: 0039_household_memories
Revises: 0038_budgets
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_household_memories"
down_revision: str | None = "0038_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "household_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        # Informational only — deliberately NO foreign key: a memory must
        # survive the deletion of the conversation it was learned from.
        sa.Column("source_conversation_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Stable keys make a restated fact an update, not a duplicate.
    op.create_index(
        "uq_household_memories_household_key",
        "household_memories",
        ["household_id", "key"],
        unique=True,
    )
    # Rolling summary of turns older than the chat history window.
    op.add_column("conversations", sa.Column("summary", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "summary")
    op.drop_index("uq_household_memories_household_key", table_name="household_memories")
    op.drop_table("household_memories")
