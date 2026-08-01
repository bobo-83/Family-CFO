"""ai_runtime_configs.cluster_enabled: opt in to the paired second box

A second GB10 Spark over the ConnectX link doubles the model-memory pool
(ADR 0071). Detection is automatic once the peer is enrolled; USING it for
larger models is the household's explicit choice, stored here.

Additive: existing rows default to off.

Revision ID: 0071_cluster_enabled
Revises: 0070_rsu_grants
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_cluster_enabled"
down_revision: str | None = "0070_rsu_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_runtime_configs",
        sa.Column("cluster_enabled", sa.Boolean, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("ai_runtime_configs", "cluster_enabled")
