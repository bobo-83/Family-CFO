"""goal monthly contribution

M118 (ADR 0027 follow-up): a goal can declare a planned monthly contribution,
which the month spending plan reserves as its "planned savings" term.

Revision ID: 0058_goal_contribution
Revises: 0057_audit_summaries
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058_goal_contribution"
down_revision: str | None = "0057_audit_summaries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "goals",
        sa.Column("monthly_contribution_minor", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("goals", "monthly_contribution_minor")
