"""emergency fund designation on accounts (M36)

Revision ID: 0034_emergency_fund
Revises: 0033_connections_dedupe
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_emergency_fund"
down_revision: str | None = "0033_connections_dedupe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("emergency_fund_percent", sa.Float, nullable=True))
    op.add_column("accounts", sa.Column("emergency_fund_minor", sa.BigInteger, nullable=True))
    # Percent-of-balance and fixed-amount designations are mutually exclusive.
    with op.batch_alter_table("accounts") as batch:
        batch.create_check_constraint(
            "ck_accounts_emergency_fund_exclusive",
            "emergency_fund_percent IS NULL OR emergency_fund_minor IS NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_constraint("ck_accounts_emergency_fund_exclusive", type_="check")
    op.drop_column("accounts", "emergency_fund_minor")
    op.drop_column("accounts", "emergency_fund_percent")
