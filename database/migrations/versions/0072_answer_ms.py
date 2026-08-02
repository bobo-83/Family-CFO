"""recommendations.answer_ms: felt latency per advisor answer

Wall-clock ms from question to persisted answer (tool loop included).
Medians per model feed the AI runtime page so model choice can rest on
evidence instead of tokens-per-second folklore.

Revision ID: 0072_answer_ms
Revises: 0071_cluster_enabled
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072_answer_ms"
down_revision: str | None = "0071_cluster_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("answer_ms", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("recommendations", "answer_ms")
