"""households.language: the language every surface answers in (#10)

Compile-time web i18n ships one build per locale, so language is a household
setting, not a per-member toggle: the box serves the build matching this
column. Null means "en" — existing households keep English until they choose.

Revision ID: 0083_household_language
Revises: 0082_savings_contributions
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0083_household_language"
down_revision: str | None = "0082_savings_contributions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("households", sa.Column("language", sa.String(5), nullable=True))


def downgrade() -> None:
    op.drop_column("households", "language")
