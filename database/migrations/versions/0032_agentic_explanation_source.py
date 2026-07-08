"""agentic_tool_calling explanation source

Revision ID: 0032_agentic_explanation
Revises: 0031_future_value_calc_type
Create Date: 2026-07-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032_agentic_explanation"
down_revision: str | None = "0031_future_value_calc_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_EXPLANATION_SOURCES = ("deterministic_stub", "llm")
NEW_EXPLANATION_SOURCES = (*OLD_EXPLANATION_SOURCES, "agentic_tool_calling")

# Both tables share the same allowed-source vocabulary (models.EXPLANATION_SOURCES).
_TABLES = (
    ("recommendations", "ck_recommendations_explanation_source"),
    ("reports", "ck_reports_explanation_source"),
)


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def _rebuild(sources: tuple[str, ...]) -> None:
    for table, constraint in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="check")
            batch.create_check_constraint(
                constraint, f"explanation_source in {_sql_in(sources)}"
            )


def upgrade() -> None:
    _rebuild(NEW_EXPLANATION_SOURCES)


def downgrade() -> None:
    _rebuild(OLD_EXPLANATION_SOURCES)
