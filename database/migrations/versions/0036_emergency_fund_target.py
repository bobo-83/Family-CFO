"""configurable emergency-fund target per household (M43)

Revision ID: 0036_emergency_fund_target
Revises: 0035_net_worth_snapshots
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_emergency_fund_target"
down_revision: str | None = "0035_net_worth_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL means "use the default target" (finance_service constant).
    op.add_column(
        "households",
        sa.Column("emergency_fund_target_months", sa.Float, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("households", "emergency_fund_target_months")
