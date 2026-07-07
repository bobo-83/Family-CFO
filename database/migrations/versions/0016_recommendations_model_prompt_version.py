"""recommendations model and prompt version tracking

Revision ID: 0016_recommendations_model_prompt_version
Revises: 0015_recommendations
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_recommendations_model_prompt_version"
down_revision: str | None = "0015_recommendations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_EXPLANATION_SOURCES = ("deterministic_stub",)
NEW_EXPLANATION_SOURCES = ("deterministic_stub", "llm")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.add_column("recommendations", sa.Column("model_version", sa.String(100), nullable=True))
    op.add_column("recommendations", sa.Column("prompt_version", sa.String(50), nullable=True))

    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.drop_constraint("ck_recommendations_explanation_source", type_="check")
        batch_op.create_check_constraint(
            "ck_recommendations_explanation_source",
            f"explanation_source in {_sql_in(NEW_EXPLANATION_SOURCES)}",
        )


def downgrade() -> None:
    with op.batch_alter_table("recommendations") as batch_op:
        batch_op.drop_constraint("ck_recommendations_explanation_source", type_="check")
        batch_op.create_check_constraint(
            "ck_recommendations_explanation_source",
            f"explanation_source in {_sql_in(OLD_EXPLANATION_SOURCES)}",
        )

    op.drop_column("recommendations", "prompt_version")
    op.drop_column("recommendations", "model_version")
