"""pairing sessions and paired devices

Revision ID: 0018_pairing_and_devices
Revises: 0017_ai_runtime_configs
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_pairing_and_devices"
down_revision: str | None = "0017_ai_runtime_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pairing_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("qr_payload", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "paired_devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("public_key", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("auth_sessions") as batch_op:
        batch_op.add_column(sa.Column("device_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key("fk_auth_sessions_device_id", "paired_devices", ["device_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("auth_sessions") as batch_op:
        batch_op.drop_constraint("fk_auth_sessions_device_id", type_="foreignkey")
        batch_op.drop_column("device_id")

    op.drop_table("paired_devices")
    op.drop_table("pairing_sessions")
