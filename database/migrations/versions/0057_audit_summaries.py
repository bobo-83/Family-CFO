"""backfill vague audit summaries

The Activity/History screen (M101) surfaces audit summaries verbatim. Older rows
were written with generic text ("Attached an image", "Updated a transaction");
this rewrites those to name the transaction they point at, so the existing
history reads as clearly as new entries. Display-only text — no downgrade needed.

Revision ID: 0057_audit_summaries
Revises: 0056_audit_undo
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0057_audit_summaries"
down_revision: str | None = "0056_audit_undo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE audit_events AS ae
        SET summary = 'Attached a photo to “'
            || COALESCE(NULLIF(t.merchant, ''), NULLIF(t.description, ''), 'a transaction')
            || '”'
        FROM transactions AS t
        WHERE ae.action = 'transaction.attachment_added'
          AND ae.summary = 'Attached an image'
          AND t.id = ae.entity_id
        """
    )
    op.execute(
        """
        UPDATE audit_events AS ae
        SET summary = 'Updated “'
            || COALESCE(NULLIF(t.merchant, ''), NULLIF(t.description, ''), 'a transaction')
            || '”'
        FROM transactions AS t
        WHERE ae.action = 'transaction.updated'
          AND ae.summary = 'Updated a transaction'
          AND t.id = ae.entity_id
        """
    )


def downgrade() -> None:
    # Summaries are display-only; the original generic text carries no data worth
    # restoring.
    pass
