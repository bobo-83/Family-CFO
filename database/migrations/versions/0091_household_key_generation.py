"""household_keys.key_generation: make a key change visible to other processes

The incident this closes: a household was sealed and then its key rotated, both
by the API process. The background worker had cached the DEK before either
event and — because ``_resolve_dek`` returned its process-local cache before
consulting anything else — never noticed. It went on reading and *writing*
rows under a key the database had already retired, for ~34 hours, until a
restart cleared the cache. 225 values were left unreadable.

A counter on the key row is the cheap fix: seal, unseal, and rotate bump it,
and every process revalidates its cached DEK against it on a short interval.
Nothing has to be pushed to anyone; a stale cache disproves itself.

Starts at 1 for existing rows — the value is meaningless in absolute terms, only
changes matter.

Revision ID: 0091_household_key_generation
Revises: 0090_audit_reverted_by
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0091_household_key_generation"
down_revision: str | None = "0090_audit_reverted_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("household_keys") as batch:
        batch.add_column(
            sa.Column("key_generation", sa.Integer(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("household_keys") as batch:
        batch.drop_column("key_generation")
