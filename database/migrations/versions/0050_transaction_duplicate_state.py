"""transaction duplicate_state for the Review queue (M97)

Bank feeds sometimes deliver two truly-identical charges (same account, date,
amount, merchant) with different provider ids — a merchant double-charge, or the
occasional bridge quirk. `duplicate_state` drives a Review screen: detection sets
'flagged' on every member of such a group; the user resolves each to 'dismissed'
(a legitimate repeat — never re-flag) or 'disputed' (contesting with the bank,
kept visible until resolved). NULL = ordinary transaction.

Additive and nullable: existing rows stay NULL. The older `possible_duplicate`
boolean is left in place, unused by this flow.

Revision ID: 0050_transaction_duplicate_state
Revises: 0049_overview_snapshots
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_transaction_duplicate_state"
down_revision: str | None = "0049_overview_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("duplicate_state", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "duplicate_state")
