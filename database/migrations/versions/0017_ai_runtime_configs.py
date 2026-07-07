"""ai_runtime_configs

Revision ID: 0017_ai_runtime_configs
Revises: 0016_recommendations_model_prompt_version
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_ai_runtime_configs"
down_revision: str | None = "0016_recommendations_model_prompt_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AI_RUNTIME_PROVIDERS = ("vllm", "ollama", "llama_cpp", "openai_compatible")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "ai_runtime_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False, unique=True
        ),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("base_url", sa.String(255), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"provider in {_sql_in(AI_RUNTIME_PROVIDERS)}",
            name="ck_ai_runtime_configs_provider",
        ),
    )


def downgrade() -> None:
    op.drop_table("ai_runtime_configs")
