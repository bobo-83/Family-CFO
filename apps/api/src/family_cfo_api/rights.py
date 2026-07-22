"""The rights catalog and built-in role presets (ADR 0034).

RIGHTS are the atomic capabilities the code enforces; ROLES (stored per
household) bundle rights; users are assigned a role. The catalog lives in code
because a right only means something where an endpoint or screen enforces it —
a database row can't invent an enforcement point.
"""

from __future__ import annotations

# --- View & advisor ----------------------------------------------------------
FINANCES_VIEW = "finances.view"
ADVISOR_USE = "advisor.use"
# Curate the advisor: teach/forget household memories, delete conversations.
ADVISOR_MANAGE = "advisor.manage"

# --- Money editing -----------------------------------------------------------
TRANSACTIONS_MANAGE = "transactions.manage"
BILLS_MANAGE = "bills.manage"
BUDGETS_MANAGE = "budgets.manage"
GOALS_MANAGE = "goals.manage"
CATEGORIES_MANAGE = "categories.manage"
INCOME_MANAGE = "income.manage"
IMPORTS_MANAGE = "imports.manage"
REPORTS_MANAGE = "reports.manage"

# --- Accounts, loans & sync --------------------------------------------------
ACCOUNTS_MANAGE = "accounts.manage"
CONNECTIONS_MANAGE = "connections.manage"

# --- Household administration ------------------------------------------------
MEMBERS_MANAGE = "members.manage"
ROLES_MANAGE = "roles.manage"
DEVICES_MANAGE = "devices.manage"
BACKUPS_MANAGE = "backups.manage"
AUDIT_VIEW = "audit.view"
HOUSEHOLD_SETTINGS_MANAGE = "household.settings.manage"

# --- Box-level (ADR 0065) -----------------------------------------------------
# These are NOT household-role rights: one vLLM serves every household, so
# runtime control belongs to the box's SYSTEM ADMINS (users, not roles). They
# are injected into a session's effective rights when the user is a system
# admin, and stripped from any household role that still carries the legacy
# ai_runtime.manage string — a household role must never grant them.
AI_RUNTIME_MANAGE = "ai_runtime.manage"
SYSTEM_ADMIN = "system.admin"
BOX_RIGHTS: frozenset[str] = frozenset({AI_RUNTIME_MANAGE, BACKUPS_MANAGE, SYSTEM_ADMIN})

ALL_RIGHTS: frozenset[str] = frozenset(
    {
        FINANCES_VIEW,
        ADVISOR_USE,
        ADVISOR_MANAGE,
        TRANSACTIONS_MANAGE,
        BILLS_MANAGE,
        BUDGETS_MANAGE,
        GOALS_MANAGE,
        CATEGORIES_MANAGE,
        INCOME_MANAGE,
        IMPORTS_MANAGE,
        REPORTS_MANAGE,
        ACCOUNTS_MANAGE,
        CONNECTIONS_MANAGE,
        MEMBERS_MANAGE,
        ROLES_MANAGE,
        DEVICES_MANAGE,
        AUDIT_VIEW,
        HOUSEHOLD_SETTINGS_MANAGE,
    }
)

# Built-in role presets, seeded per household (migration 0060). Admin is the
# complete, immutable superset; User deliberately lacks accounts/imports/
# connections and every admin right — a User edits budgets and bills, never the
# balance sheet or the machinery.
PRESET_ADMIN = "Admin"
PRESET_USER = "User"
PRESET_VIEWER = "Viewer"
PRESET_CHILD = "Child"

PRESET_RIGHTS: dict[str, frozenset[str]] = {
    PRESET_ADMIN: ALL_RIGHTS,
    PRESET_USER: frozenset(
        {
            FINANCES_VIEW,
            ADVISOR_USE,
            ADVISOR_MANAGE,
            TRANSACTIONS_MANAGE,
            BILLS_MANAGE,
            BUDGETS_MANAGE,
            GOALS_MANAGE,
            CATEGORIES_MANAGE,
            INCOME_MANAGE,
        }
    ),
    PRESET_VIEWER: frozenset({FINANCES_VIEW, ADVISOR_USE}),
    PRESET_CHILD: frozenset({FINANCES_VIEW}),
}

# Legacy role string -> preset role name (backfill + old device credentials).
LEGACY_ROLE_TO_PRESET: dict[str, str] = {
    "owner": PRESET_ADMIN,
    "adult": PRESET_USER,
    "viewer": PRESET_VIEWER,
    "child": PRESET_CHILD,
}

# Preset name -> legacy role string kept on the wire for compatibility.
PRESET_TO_LEGACY_ROLE: dict[str, str] = {v: k for k, v in LEGACY_ROLE_TO_PRESET.items()}


def rights_for_legacy_role(role: str | None) -> frozenset[str]:
    """The preset rights an old credential (role string only) falls back to."""
    preset = LEGACY_ROLE_TO_PRESET.get(role or "")
    return PRESET_RIGHTS.get(preset or "", frozenset())
