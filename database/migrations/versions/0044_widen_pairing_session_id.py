"""widen pairing_sessions.id for the CSPRNG bearer token

The pairing session id is not a UUID — it is the QR-borne bearer secret
(`secrets.token_urlsafe(32)` ≈ 43 chars). The column was created as
VARCHAR(36), which SQLite ignores but PostgreSQL enforces, so creating a
pairing code failed with StringDataRightTruncation on real deployments.

Revision ID: 0044_widen_pairing_session_id
Revises: 0043_income_profiles
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_widen_pairing_session_id"
down_revision: str | None = "0043_income_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch mode so SQLite (no ALTER COLUMN TYPE) recreates the table while
    # PostgreSQL runs a plain ALTER.
    with op.batch_alter_table("pairing_sessions") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.String(36),
            type_=sa.String(64),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("pairing_sessions") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.String(64),
            type_=sa.String(36),
            existing_nullable=False,
        )
