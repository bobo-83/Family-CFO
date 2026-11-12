"""Store short-lived existing-device reauthentication challenges (ADR 0080)."""

from alembic import op
import sqlalchemy as sa

revision = "0098_device_reauth_challenges"
down_revision = "0097_retire_qr_pairing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_reauth_challenges",
        sa.Column("challenge_hash", sa.String(64), primary_key=True),
        sa.Column("device_id", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_device_reauth_challenges_expires_at",
        "device_reauth_challenges",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_table("device_reauth_challenges")
