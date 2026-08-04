"""Sealed mode: households.sealed_mode, key canary, nullable box wrap

ADR 0072 Phase 3. A sealed household's row in household_keys keeps
wrapped_dek NULL — the box holds no wrap; the data key exists only in the
server's session keyring while a member or device session is live. The
canary (rows-subkey ciphertext of a fixed plaintext) validates keys posted
by devices or unwrapped from member passwords before they are trusted.

Revision ID: 0079_sealed_mode
Revises: 0078_household_key_wraps
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0079_sealed_mode"
down_revision: str | None = "0078_household_key_wraps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("households", sa.Column("sealed_mode", sa.Boolean, nullable=True))
    op.add_column("household_keys", sa.Column("canary", sa.Text, nullable=True))
    with op.batch_alter_table("household_keys") as batch:
        batch.alter_column("wrapped_dek", existing_type=sa.Text, nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("household_keys") as batch:
        batch.alter_column("wrapped_dek", existing_type=sa.Text, nullable=False)
    op.drop_column("household_keys", "canary")
    op.drop_column("households", "sealed_mode")
