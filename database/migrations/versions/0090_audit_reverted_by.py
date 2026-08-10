"""audit_events.reverted_by: who undid it (#61)

Reversing an action was the one operation the audit log could not account for:
``mark_audit_reverted`` wrote only ``reverted_at``, so the log said an action
had been undone but never by whom — the question you ask precisely when
something looks wrong.

Nullable because rows reverted before this migration have no honest answer, and
because the column is null for every event that was never reverted at all.

Revision ID: 0090_audit_reverted_by
Revises: 0089_household_timezone
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090_audit_reverted_by"
down_revision: str | None = "0089_household_timezone"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch: SQLite cannot ALTER-ADD a foreign-keyed column (same as 0084).
    with op.batch_alter_table("audit_events") as batch:
        batch.add_column(sa.Column("reverted_by", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_audit_events_reverted_by", "users", ["reverted_by"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_constraint("fk_audit_events_reverted_by", type_="foreignkey")
        batch.drop_column("reverted_by")
