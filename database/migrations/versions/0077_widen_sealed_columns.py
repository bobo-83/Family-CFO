"""Widen sealed content columns to Text: ciphertext outgrows VARCHAR(n)

A Fernet token for even a short merchant name is ~130 characters, so every
sealed column (ADR 0072 batch 2) must be Text. Postgres enforces VARCHAR
lengths (the SQLite test engine does not, which is why this surfaced on the
box, not in CI). Plaintext length limits stay enforced at the API boundary
by the request schemas.

Revision ID: 0077_widen_sealed_columns
Revises: 0076_household_keys
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0077_widen_sealed_columns"
down_revision: str | None = "0076_household_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WIDEN = [
    ("accounts", "name", sa.String(120), False),
    ("accounts", "institution", sa.String(120), True),
    ("transactions", "merchant", sa.String(120), True),
    ("bills", "name", sa.String(120), False),
    ("income_sources", "name", sa.String(120), False),
    ("goals", "name", sa.String(120), False),
    ("household_memories", "value", sa.String(500), False),
    ("advisor_feedback", "note", sa.String(500), True),
]


def upgrade() -> None:
    # batch_alter_table: plain ALTER on Postgres, table-copy on SQLite (which
    # has no ALTER COLUMN TYPE).
    for table, column, _old, nullable in _WIDEN:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, type_=sa.Text, existing_nullable=nullable)


def downgrade() -> None:
    # Only safe on data written before sealing; ciphertext will not fit back.
    for table, column, old, nullable in _WIDEN:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, type_=old, existing_nullable=nullable)
