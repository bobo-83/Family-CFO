"""households.timezone: the household's own idea of "today" (#41)

Every date calculation used the API container's date, and that container runs
UTC. For a household in EDT that means the app believes it is tomorrow from
8pm local — "due soon", overdue flags, safe-to-spend's horizon and statement
due dates all shift a day early each evening.

One box can also host households in different zones, where a single shared
"today" is wrong for at least one of them by construction.

Null means "use the box's configured default", so existing households keep
behaving exactly as they did until a zone is chosen.

Revision ID: 0089_household_timezone
Revises: 0088_statement_lines
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0089_household_timezone"
down_revision: str | None = "0088_statement_lines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("households", sa.Column("timezone", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "timezone")
