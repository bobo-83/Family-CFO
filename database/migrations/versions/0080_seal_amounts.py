"""transactions.amount_minor becomes sealed Text (issue #184)

Completes ADR 0072 Phase 1: the last recorded plaintext deviation. Amounts
join the sealed columns — every aggregation already moved to
decrypt-then-compute in the repository, so SQL never needs the number.
Legacy rows become digit strings (readable transparently) until
`python -m family_cfo_api.tools.encrypt_existing` seals them.

Revision ID: 0080_seal_amounts
Revises: 0079_sealed_mode
Create Date: 2026-08-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0080_seal_amounts"
down_revision: str | None = "0079_sealed_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch:
        batch.alter_column(
            "amount_minor",
            type_=sa.Text,
            existing_type=sa.BigInteger,
            existing_nullable=False,
            postgresql_using="amount_minor::text",
        )


def downgrade() -> None:
    # Only safe before sealing ran: enc1: tokens will not cast back to bigint.
    with op.batch_alter_table("transactions") as batch:
        batch.alter_column(
            "amount_minor",
            type_=sa.BigInteger,
            existing_type=sa.Text,
            existing_nullable=False,
            postgresql_using="amount_minor::bigint",
        )
