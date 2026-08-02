"""accounts.rsu_ready_to_sell: tag an account's balance as sellable RSUs

The user tags the brokerage account holding vested shares; safe-to-spend
shows that synced balance beside the headline ("one sale from cash") —
ground truth from the provider, not schedule guesswork.

Revision ID: 0074_rsu_ready_to_sell
Revises: 0073_backup_versioning
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0074_rsu_ready_to_sell"
down_revision: str | None = "0073_backup_versioning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("rsu_ready_to_sell", sa.Boolean, nullable=True))


def downgrade() -> None:
    op.drop_column("accounts", "rsu_ready_to_sell")
