"""Add opaque backup-settings revisions (issue #116, ADR 0078).

Revision ID: 0095_backup_settings_revision
Revises: 0094_backup_delete_intents
Create Date: 2026-09-14
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0095_backup_settings_revision"
down_revision: str | None = "0094_backup_delete_intents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("backup_settings") as batch:
        batch.add_column(sa.Column("revision", sa.String(length=36), nullable=True))

    bind = op.get_bind()
    settings = sa.table(
        "backup_settings",
        sa.column("key", sa.String(length=16)),
        sa.column("revision", sa.String(length=36)),
    )
    keys = bind.execute(sa.select(settings.c.key)).scalars().all()
    for key in keys:
        bind.execute(
            sa.update(settings).where(settings.c.key == key).values(revision=str(uuid.uuid4()))
        )

    with op.batch_alter_table("backup_settings") as batch:
        batch.alter_column(
            "revision",
            existing_type=sa.String(length=36),
            nullable=False,
        )
        batch.create_check_constraint(
            "ck_backup_settings_revision_length",
            "length(revision) = 36",
        )


def downgrade() -> None:
    with op.batch_alter_table("backup_settings") as batch:
        batch.drop_constraint("ck_backup_settings_revision_length", type_="check")
        batch.drop_column("revision")
