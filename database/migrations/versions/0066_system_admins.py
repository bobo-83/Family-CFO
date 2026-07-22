"""box-level system administrators

ADR 0065: model swaps (and future box-global actions) are guarded by a
SYSTEM ADMIN role that lives on the user, not on a household role — one vLLM
serves every household, so no single household's role may control it. The
first household's owner is granted automatically at bootstrap; admins manage
the roster afterwards (grant by email, revoke — never the last one).

Revision ID: 0066_system_admins
Revises: 0065_retirement_age_solve_calculation
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_system_admins"
down_revision: str | None = "0065_retirement_age_solve"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "system_admins",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "granted_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("system_admins")
