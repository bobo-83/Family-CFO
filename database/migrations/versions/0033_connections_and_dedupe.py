"""institution connections + transaction dedupe columns (M27, ADR 0015)

Revision ID: 0033_connections_dedupe
Revises: 0032_agentic_explanation
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_connections_dedupe"
down_revision: str | None = "0032_agentic_explanation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("external_id", sa.String(120), nullable=True))
    op.add_column("transactions", sa.Column("import_hash", sa.String(64), nullable=True))
    op.create_index(
        "uq_transactions_account_external",
        "transactions",
        ["account_id", "external_id"],
        unique=True,
        sqlite_where=sa.text("external_id IS NOT NULL"),
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index("ix_transactions_import_hash", "transactions", ["account_id", "import_hash"])

    op.create_table(
        "institution_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("access_url_encrypted", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "connection_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey(
                "institution_connections.id", name="fk_connection_accounts_connection_id"
            ),
            nullable=False,
        ),
        sa.Column("external_account_id", sa.String(120), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_connection_accounts_external",
        "connection_accounts",
        ["connection_id", "external_account_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_connection_accounts_external", table_name="connection_accounts")
    op.drop_table("connection_accounts")
    op.drop_table("institution_connections")
    op.drop_index("ix_transactions_import_hash", table_name="transactions")
    op.drop_index("uq_transactions_account_external", table_name="transactions")
    op.drop_column("transactions", "import_hash")
    op.drop_column("transactions", "external_id")
