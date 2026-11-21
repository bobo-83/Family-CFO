"""ai_runtime_apply_receipts

Issue #170 (ADR 0081): one idempotent commit receipt per apply job — the only
SQL record of a replacement. The manager's filesystem journal owns execution
state; PostgreSQL owns household settings plus this exactly-once receipt
(job identity, runtime key, plan hash, initiator attribution, source,
committed model, commit time). No mutable status column and no copied journal.

Also adds the audit-deduplication uniqueness: final apply outcome audits are
deduplicated by job ID + action via a partial unique index on audit_events
scoped to the apply-job entity type, so reconciler races and retries cannot
write duplicate outcomes.

No backfill: historical applies get no fabricated receipt.

Downgrade is restricted (spec 05): only after all v2 jobs are drained and the
older application no longer references the table; operational records are
retained normally until then.

Revision ID: 0099_ai_runtime_apply_receipts
Revises: 0098_device_reauth_challenges
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0099_ai_runtime_apply_receipts"
down_revision: str | None = "0098_device_reauth_challenges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The audit entity type that carries apply-job identity: every apply outcome
# audit writes entity_type="ai_runtime_apply_job", entity_id=<job id>, so a
# partial unique index on (action, entity_id) within that entity type is the
# internal uniqueness key (job ID + action).
APPLY_JOB_ENTITY_TYPE = "ai_runtime_apply_job"

APPLY_SOURCES = ("api", "cli")


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "ai_runtime_apply_receipts",
        # The codebase represents UUIDs as 36-char strings everywhere; this
        # keeps the table portable to the SQLite test path.
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column("runtime_key", sa.String(255), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("initiator_household_id", sa.String(36), nullable=True),
        sa.Column("initiator_user_id", sa.String(36), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("committed_model", sa.String(255), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"source in {_sql_in(APPLY_SOURCES)}",
            name="ck_ai_runtime_apply_receipts_source",
        ),
    )
    op.create_index(
        "uq_audit_apply_outcome_job_action",
        "audit_events",
        ["action", "entity_id"],
        unique=True,
        sqlite_where=sa.text(f"entity_type = '{APPLY_JOB_ENTITY_TYPE}'"),
        postgresql_where=sa.text(f"entity_type = '{APPLY_JOB_ENTITY_TYPE}'"),
    )


def downgrade() -> None:
    op.drop_index("uq_audit_apply_outcome_job_action", table_name="audit_events")
    op.drop_table("ai_runtime_apply_receipts")
