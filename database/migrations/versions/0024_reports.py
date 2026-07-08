"""reports

Revision ID: 0024_reports
Revises: 0023_transactions_import_id
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_reports"
down_revision: str | None = "0023_transactions_import_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPORT_TYPES = ("weekly", "monthly")
EXPLANATION_SOURCES = ("deterministic_stub", "llm")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False),
        sa.Column("report_type", sa.String(20), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),
        sa.Column("summary_json", sa.JSON, nullable=False),
        sa.Column("explanation_text", sa.Text, nullable=False),
        sa.Column("explanation_source", sa.String(30), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("calculation_version", sa.String(20), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "household_id", "report_type", "period_start", name="uq_reports_household_type_period"
        ),
        sa.CheckConstraint(f"report_type in {_sql_in(REPORT_TYPES)}", name="ck_reports_type"),
        sa.CheckConstraint(
            f"explanation_source in {_sql_in(EXPLANATION_SOURCES)}", name="ck_reports_explanation_source"
        ),
    )


def downgrade() -> None:
    op.drop_table("reports")
