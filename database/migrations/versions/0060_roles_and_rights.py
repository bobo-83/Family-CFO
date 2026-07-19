"""per-household roles bundling rights

ADR 0034: RIGHTS (atomic capabilities) bundle into ROLES; users get a role.
Seeds the built-in presets (Admin/User/Viewer/Child) for every existing
household and backfills memberships.role_id from the legacy role string.

Revision ID: 0060_roles_and_rights
Revises: 0059_account_next_payment_due
Create Date: 2026-07-19
"""

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0060_roles_and_rights"
down_revision: str | None = "0059_account_next_payment_due"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "household_id", sa.String(36), sa.ForeignKey("households.id"), nullable=False
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("rights_json", sa.JSON(), nullable=False),
        sa.Column("built_in", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("household_id", "name", name="uq_roles_household_name"),
    )
    with op.batch_alter_table("household_memberships") as batch_op:
        batch_op.add_column(
            sa.Column(
                "role_id",
                sa.String(36),
                sa.ForeignKey("roles.id", name="fk_household_memberships_role_id"),
                nullable=True,
            )
        )

    # Seed presets per existing household and backfill memberships. The preset
    # definitions are INLINED so this migration stays frozen even if the
    # catalog evolves later (later changes ship as later migrations).
    PRESET_RIGHTS = {
        "Admin": [
                "accounts.manage",
                "advisor.manage",
                "advisor.use",
                "ai_runtime.manage",
                "audit.view",
                "backups.manage",
                "bills.manage",
                "budgets.manage",
                "categories.manage",
                "connections.manage",
                "devices.manage",
                "finances.view",
                "goals.manage",
                "household.settings.manage",
                "imports.manage",
                "income.manage",
                "members.manage",
                "reports.manage",
                "roles.manage",
                "transactions.manage"
        ],
        "Child": [
                "finances.view"
        ],
        "User": [
                "advisor.manage",
                "advisor.use",
                "bills.manage",
                "budgets.manage",
                "categories.manage",
                "finances.view",
                "goals.manage",
                "income.manage",
                "transactions.manage"
        ],
        "Viewer": [
                "advisor.use",
                "finances.view"
        ]
}
    LEGACY_ROLE_TO_PRESET = {
        "adult": "User",
        "child": "Child",
        "owner": "Admin",
        "viewer": "Viewer"
}

    conn = op.get_bind()
    now = datetime.now(UTC)
    households = conn.execute(sa.text("SELECT id FROM households")).fetchall()
    for (household_id,) in households:
        preset_ids: dict[str, str] = {}
        for name, rights in PRESET_RIGHTS.items():
            role_id = str(uuid.uuid4())
            preset_ids[name] = role_id
            conn.execute(
                sa.text(
                    "INSERT INTO roles (id, household_id, name, rights_json, built_in,"
                    " created_at, updated_at)"
                    " VALUES (:id, :hh, :name, :rights, :built_in, :now, :now)"
                ),
                {
                    "id": role_id,
                    "hh": household_id,
                    "name": name,
                    "rights": json.dumps(sorted(rights)),
                    "built_in": True,
                    "now": now,
                },
            )
        for legacy, preset in LEGACY_ROLE_TO_PRESET.items():
            conn.execute(
                sa.text(
                    "UPDATE household_memberships SET role_id = :role_id"
                    " WHERE household_id = :hh AND role = :legacy"
                ),
                {"role_id": preset_ids[preset], "hh": household_id, "legacy": legacy},
            )


def downgrade() -> None:
    with op.batch_alter_table("household_memberships") as batch_op:
        batch_op.drop_column("role_id")
    op.drop_table("roles")
