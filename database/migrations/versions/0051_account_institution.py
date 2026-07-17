"""per-account institution name (M97)

SimpleFIN aggregates several banks under one connection whose display_name is a
generic "SimpleFin (multiple banks)", so it can't tell an Amex card from a Schwab
account. Each SimpleFIN account carries its own `org` (the real institution); we
store it per account so the app can say where to look a transaction up.

Additive and nullable: existing accounts stay NULL until the next sync backfills
them from the provider's org.

Revision ID: 0051_account_institution
Revises: 0050_transaction_duplicate_state
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_account_institution"
down_revision: str | None = "0050_transaction_duplicate_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("institution", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "institution")
