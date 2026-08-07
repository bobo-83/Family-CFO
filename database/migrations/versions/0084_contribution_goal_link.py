"""savings_contributions.goal_id: which goal a contribution funds (#4)

A goal's progress bar and a detected/declared contribution filling it were
strangers: the link makes funding visible ("$X/mo going in") and its absence
loud ("nothing is currently funding this goal"). A contribution funds at most
one goal; a goal may be funded by several contributions.

Revision ID: 0084_contribution_goal_link
Revises: 0083_household_language
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0084_contribution_goal_link"
down_revision: str | None = "0083_household_language"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch: SQLite cannot ALTER-ADD a foreign-keyed column (same as 0077).
    with op.batch_alter_table("savings_contributions") as batch:
        batch.add_column(sa.Column("goal_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_savings_contributions_goal", "goals", ["goal_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("savings_contributions") as batch:
        batch.drop_constraint("fk_savings_contributions_goal", type_="foreignkey")
        batch.drop_column("goal_id")
