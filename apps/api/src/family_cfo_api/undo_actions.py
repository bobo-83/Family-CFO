"""Undo support for the Activity/History screen (M108).

Every reversible mutation records an ``undo_token`` (JSON) on its audit event
describing how to reverse it; :func:`reverse` applies the inverse. The three
generic shapes cover create/update/delete on the household's own records:

- ``{"op": "delete", "entity": E, "id": ID}``          — reverse of a CREATE
- ``{"op": "recreate", "entity": E, "data": {...}}``    — reverse of a DELETE
- ``{"op": "restore", "entity": E, "id": ID, "data"}``  — reverse of an UPDATE

Plus one special case for a transaction recategorize (it restores the prior
category id). Inherently-irreversible actions (login, a backup/restore that ran, a
revealed key) record no token and simply aren't undoable — the UI shows no Undo.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy.engine import Engine

from family_cfo_api import repository


class UndoError(Exception):
    """A token can't be reversed — unknown shape, or the target no longer exists."""


# --- undo-completeness policy (ADR 0023) ------------------------------------
#
# Every audit action must be classified here, and `audit.write_audit` refuses an
# action that isn't — so a new mutation cannot ship without a deliberate decision
# about whether the user can undo it. The rule: an action may only be
# IRREVERSIBLE if it has a real-world side effect that genuinely cannot be
# reversed (a login, a paired device, a snapshot written to the NAS, a secret
# shown, a document produced) or would require replaying a secret we refuse to
# store. Everything that only changes household state MUST be UNDOABLE. PENDING is
# tracked debt: state changes that should be undoable but aren't wired yet — new
# actions may not join it silently (the test freezes the set).

UNDOABLE = "undoable"
IRREVERSIBLE = "irreversible"
PENDING = "pending"

UNDO_POLICY: dict[str, str] = {
    # transactions
    "transaction.updated": UNDOABLE,
    "transaction.deleted": UNDOABLE,
    "transaction.created": UNDOABLE,
    "transaction.attachment_added": UNDOABLE,
    # bills
    "bill.created": UNDOABLE,
    "bill.updated": UNDOABLE,
    "bill.deleted": UNDOABLE,
    "bill_suggestion.dismissed": UNDOABLE,
    # categories
    "category.created": UNDOABLE,
    "category.updated": UNDOABLE,
    "category.deleted": UNDOABLE,
    # accounts
    "account.created": UNDOABLE,
    "account.updated": UNDOABLE,
    "account.deleted": UNDOABLE,
    "account.balance_recorded": UNDOABLE,
    # budgets
    "budget.created": UNDOABLE,
    "budget.updated": UNDOABLE,
    "budget.deleted": UNDOABLE,
    # income
    "income.created": UNDOABLE,
    "income.updated": UNDOABLE,
    "income.deleted": UNDOABLE,
    # advisor memories
    "memory.created": UNDOABLE,
    "memory.deleted": UNDOABLE,
    # income analysis
    "income_override.set": UNDOABLE,
    "income_profile.created": UNDOABLE,
    "income_profile.deleted": UNDOABLE,
    "income_tax_settings.updated": UNDOABLE,
    # members
    "member.created": UNDOABLE,
    "member.removed": UNDOABLE,
    "member.role_changed": UNDOABLE,
    # roles (ADR 0034)
    "role.created": UNDOABLE,
    "role.updated": UNDOABLE,
    "role.deleted": UNDOABLE,
    # household
    "household.created": IRREVERSIBLE,  # bootstrapping the household, not an activity action
    "household.updated": UNDOABLE,
    # AI runtime
    "ai_runtime.updated": UNDOABLE,
    "ai_runtime.model_applied": IRREVERSIBLE,  # an operational model swap ran (vLLM reload)
    # bank connections
    "connection.created": UNDOABLE,
    "connection.deleted": IRREVERSIBLE,  # re-linking needs re-authorizing with the provider
    # imports
    "import.applied": UNDOABLE,
    "import.discarded": IRREVERSIBLE,  # bulk-deleted staged rows; re-upload the file to redo
    # backups — every one touches the NAS or shows a secret; none is a state undo
    "backup.created": IRREVERSIBLE,  # a snapshot was written to the NAS
    "backup.restored": IRREVERSIBLE,  # data was restored from a snapshot
    "backup.restored_remote": IRREVERSIBLE,
    "backup.deleted": IRREVERSIBLE,  # a backup file was removed from the NAS
    "backup.deleted_remote": IRREVERSIBLE,
    "backup.key_revealed": IRREVERSIBLE,  # a secret was shown; can't un-see it
    "backup.config_updated": IRREVERSIBLE,  # holds credentials we never store for replay
    "backup_job": IRREVERSIBLE,  # a scheduled backup ran
    # auth & pairing
    "auth.login": IRREVERSIBLE,  # a sign-in isn't a change to reverse
    "pairing.confirmed": IRREVERSIBLE,  # a device was paired (revoke it from Devices)
    "pairing.device_revoked": IRREVERSIBLE,  # a device was revoked (re-pair to restore)
    # goals
    "goal.created": UNDOABLE,
    "goal.updated": UNDOABLE,
    "goal.deleted": UNDOABLE,
    # reports
    "report.generated": IRREVERSIBLE,  # a document was produced; nothing to undo
}


def require_classified(action: str) -> str:
    """The action's undo policy, or raise if it isn't registered. Called by
    ``audit.write_audit`` so an unclassified action fails loudly (ADR 0023)."""
    policy = UNDO_POLICY.get(action)
    if policy is None:
        raise ValueError(
            f"audit action {action!r} has no undo policy — add it to "
            "undo_actions.UNDO_POLICY (UNDOABLE / IRREVERSIBLE / PENDING). See ADR 0023."
        )
    return policy


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


# --- token builders (called by the write handlers) --------------------------


def created(entity: str, entity_id: str) -> str:
    """A CREATE is undone by deleting the new record."""
    return json.dumps({"op": "delete", "entity": entity, "id": entity_id})


def bill_deleted(bill: repository.RecurringRecord) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "bill",
            "data": {
                "name": bill.name,
                "amount_minor": bill.amount_minor,
                "currency": bill.currency,
                "frequency": bill.frequency,
                "next_due_date": _iso(bill.next_due_date),
                "category_id": bill.category_id,
            },
        }
    )


def bill_updated(before: repository.RecurringRecord) -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "bill",
            "id": before.id,
            "data": {
                "name": before.name,
                "amount_minor": before.amount_minor,
                "currency": before.currency,
                "frequency": before.frequency,
                "next_due_date": _iso(before.next_due_date),
                "category_id": before.category_id,
            },
        }
    )


def role_updated(before: "repository.RoleRecord") -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "role",
            "id": before.id,
            "data": {"name": before.name, "rights": sorted(before.rights)},
        }
    )


def role_deleted(before: "repository.RoleRecord") -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "role",
            "data": {"id": before.id, "name": before.name, "rights": sorted(before.rights)},
        }
    )


def category_deleted(category: repository.CategoryRecord) -> str:
    return json.dumps(
        {"op": "recreate", "entity": "category", "data": {"name": category.name}}
    )


def category_updated(before: repository.CategoryRecord) -> str:
    return json.dumps(
        {"op": "restore", "entity": "category", "id": before.id, "data": {"name": before.name}}
    )


def account_deleted(account: repository.AccountRecord) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "account",
            "data": {
                "name": account.name,
                "account_type": account.account_type,
                "currency": account.currency,
                "annual_interest_rate": account.annual_interest_rate,
                "minimum_payment_minor": account.minimum_payment_minor,
                "maturity_date": _iso(account.maturity_date),
            },
        }
    )


def account_updated(before: repository.AccountRecord) -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "account",
            "id": before.id,
            "data": {
                "name": before.name,
                "account_type": before.account_type,
                "annual_interest_rate": before.annual_interest_rate,
                "minimum_payment_minor": before.minimum_payment_minor,
                "maturity_date": _iso(before.maturity_date),
                "emergency_fund_percent": before.emergency_fund_percent,
                "emergency_fund_minor": before.emergency_fund_minor,
            },
        }
    )


def budget_deleted(budget: repository.BudgetRecord) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "budget",
            "data": {
                "category_id": budget.category_id,
                "limit_minor": budget.limit_minor,
                "currency": budget.currency,
            },
        }
    )


def budget_updated(before: repository.BudgetRecord) -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "budget",
            "id": before.id,
            "data": {"limit_minor": before.limit_minor},
        }
    )


def income_deleted(income: repository.RecurringRecord) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "income",
            "data": {
                "name": income.name,
                "amount_minor": income.amount_minor,
                "currency": income.currency,
                "frequency": income.frequency,
            },
        }
    )


def income_updated(before: repository.RecurringRecord) -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "income",
            "id": before.id,
            "data": {
                "name": before.name,
                "amount_minor": before.amount_minor,
                "currency": before.currency,
                "frequency": before.frequency,
            },
        }
    )


def memory_deleted(memory: repository.HouseholdMemoryRecord) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "memory",
            "data": {"key": memory.key, "value": memory.value, "source": memory.source},
        }
    )


def transaction_recategorized(transaction_id: str, previous_category_id: str | None) -> str:
    return json.dumps(
        {
            "op": "transaction_category",
            "transaction_id": transaction_id,
            "previous_category_id": previous_category_id,
        }
    )


def transaction_updated(before: "repository.TransactionRecord") -> str:
    """Any edit to a transaction (note, merchant, description, category,
    duplicate flag, amount, account, date) is undone by restoring every mutable
    field to what it was before the edit."""
    return json.dumps(
        {
            "op": "restore",
            "entity": "transaction",
            "id": before.id,
            "data": {
                "account_id": before.account_id,
                "occurred_at": _iso(before.occurred_at),
                "amount_minor": before.amount_minor,
                "currency": before.currency,
                "merchant": before.merchant,
                "description": before.description,
                "category_id": before.category_id,
                "duplicate_state": before.duplicate_state,
                "note": before.note,
                "attachment_path": before.attachment_path,
                "attachment_content_type": before.attachment_content_type,
            },
        }
    )


def transaction_deleted(before: "repository.TransactionRecord") -> str:
    """A delete is undone by re-inserting the transaction with its prior fields —
    same aggregator id (so bank dedupe still recognises it), note, category and
    duplicate flag."""
    return json.dumps(
        {
            "op": "recreate",
            "entity": "transaction",
            "data": {
                "id": before.id,
                "account_id": before.account_id,
                "occurred_at": _iso(before.occurred_at),
                "amount_minor": before.amount_minor,
                "currency": before.currency,
                "merchant": before.merchant,
                "description": before.description,
                "category_id": before.category_id,
                "duplicate_state": before.duplicate_state,
                "external_id": before.external_id,
                "note": before.note,
                "attachment_path": before.attachment_path,
                "attachment_content_type": before.attachment_content_type,
            },
        }
    )


def goal_updated(before: "repository.GoalRecord") -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "goal",
            "id": before.id,
            "data": {
                "name": before.name,
                "target_minor": before.target_minor,
                "target_date": _iso(before.target_date),
                "priority": before.priority,
                "monthly_contribution_minor": before.monthly_contribution_minor,
            },
        }
    )


def goal_deleted(goal: "repository.GoalRecord") -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "goal",
            "data": {
                "name": goal.name,
                "goal_type": goal.goal_type,
                "target_minor": goal.target_minor,
                "current_minor": goal.current_minor,
                "currency": goal.currency,
                "target_date": _iso(goal.target_date),
                "priority": goal.priority,
                "monthly_contribution_minor": goal.monthly_contribution_minor,
            },
        }
    )


def suggestion_dismissed(merchant_key: str) -> str:
    """A dismissal is undone by removing the dismissal row (M117)."""
    return json.dumps({"op": "undismiss_suggestion", "merchant_key": merchant_key})


def balance_recorded(balance_id: str) -> str:
    """A recorded balance snapshot is undone by deleting it — the prior snapshot
    becomes current again (M117)."""
    return json.dumps({"op": "delete", "entity": "account_balance", "id": balance_id})


def income_override_set(transaction_id: str, previous_verdict: str | None) -> str:
    """Restore the previous include/exclude verdict, or clear it (M117)."""
    return json.dumps(
        {
            "op": "income_override",
            "transaction_id": transaction_id,
            "previous_verdict": previous_verdict,
        }
    )


def income_profile_deleted(profile: "repository.IncomeProfileRecord") -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "income_profile",
            "data": {
                "label": profile.label,
                "base_salary_minor": profile.base_salary_minor,
                "rsu_annual_minor": profile.rsu_annual_minor,
                "rsu_frequency": profile.rsu_frequency,
                "rsu_next_vest_date": _iso(profile.rsu_next_vest_date),
                "bonus_percent": profile.bonus_percent,
                "bonus_month": profile.bonus_month,
                "w2_year": profile.w2_year,
                "w2_wages_minor": profile.w2_wages_minor,
                "w2_withheld_minor": profile.w2_withheld_minor,
            },
        }
    )


def tax_settings_updated(before: "repository.HouseholdRecord") -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "tax_settings",
            "id": before.id,
            "data": {
                "tax_filing_status": before.tax_filing_status,
                "income_treated_as_net": before.income_treated_as_net,
                "state": before.state,
            },
        }
    )


def household_updated(before: "repository.HouseholdRecord") -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "household_settings",
            "id": before.id,
            "data": {
                "emergency_fund_target_months": before.emergency_fund_target_months,
                "credit_cards_paid_in_full": before.credit_cards_paid_in_full,
            },
        }
    )


def member_removed(user_id: str, role: str) -> str:
    """The user row survives removal; re-inserting the membership restores access."""
    return json.dumps({"op": "restore_membership", "user_id": user_id, "role": role})


def member_role_changed(user_id: str, previous_role: str) -> str:
    return json.dumps(
        {"op": "restore", "entity": "member_role", "id": user_id, "data": {"role": previous_role}}
    )


def ai_runtime_updated(before) -> str:
    """Restore the previous runtime config, or clear it when this was the first."""
    if before is None:
        return json.dumps({"op": "ai_runtime_clear"})
    return json.dumps(
        {
            "op": "restore",
            "entity": "ai_runtime",
            "id": before.household_id,
            "data": {
                "provider": before.provider,
                "base_url": before.base_url,
                "model": before.model,
                "enabled": before.enabled,
            },
        }
    )


def import_applied(import_id: str, previous_status: str) -> str:
    """Applying an import flipped its pending rows to reviewed; undo flips the
    import's transactions back to pending and restores the import's status."""
    return json.dumps(
        {"op": "unapply_import", "import_id": import_id, "previous_status": previous_status}
    )


# --- reverse dispatcher (called by the undo endpoint) -----------------------


def reverse(engine: Engine, household_id: str, token: dict[str, Any]) -> None:
    """Apply the inverse of a recorded action. Raises :class:`UndoError` if the
    token isn't a shape we can reverse or the target is gone."""
    op = token.get("op")

    # A transaction recategorize restores the prior category. ("category" is the
    # legacy key shape from M101, still present on older audit rows.)
    if op == "transaction_category" or token.get("kind") == "category":
        transaction_id = token.get("transaction_id")
        previous = token.get("previous_category_id")
        if not transaction_id or repository.get_transaction(
            engine, household_id, transaction_id
        ) is None:
            raise UndoError("the transaction no longer exists")
        repository.update_transaction(
            engine, household_id, transaction_id,
            category_id=previous, clear_category=previous is None,
        )
        return

    if op == "undismiss_suggestion":
        key = token.get("merchant_key")
        if not key:
            raise UndoError("this action can't be undone")
        repository.remove_bill_suggestion_dismissal(engine, household_id, key)
        return

    if op == "income_override":
        transaction_id = token.get("transaction_id")
        if not transaction_id:
            raise UndoError("this action can't be undone")
        previous = token.get("previous_verdict") or "clear"
        if not repository.set_income_override(engine, household_id, transaction_id, previous):
            raise UndoError("the transaction no longer exists")
        return

    if op == "restore_membership":
        user_id = token.get("user_id")
        if not user_id:
            raise UndoError("this action can't be undone")
        repository.restore_membership(
            engine, household_id, user_id, token.get("role") or "viewer"
        )
        return

    if op == "ai_runtime_clear":
        repository.delete_ai_runtime_config(engine, household_id)
        return

    if op == "unapply_import":
        import_id = token.get("import_id")
        if not import_id:
            raise UndoError("this action can't be undone")
        repository.unapply_import(
            engine, household_id, import_id, token.get("previous_status") or "parsed"
        )
        return

    entity = token.get("entity")
    data = token.get("data") or {}
    if op == "delete":
        _delete(engine, household_id, entity, token.get("id"))
    elif op == "recreate":
        _recreate(engine, household_id, entity, data)
    elif op == "restore":
        _restore(engine, household_id, entity, token.get("id"), data)
    else:
        raise UndoError("this action can't be undone")


def _delete(engine: Engine, household_id: str, entity: str | None, entity_id: str | None) -> None:
    if not entity_id:
        raise UndoError("this action can't be undone")
    if entity == "bill":
        repository.delete_bill(engine, household_id, entity_id)
    elif entity == "category":
        repository.delete_category(engine, household_id, entity_id)
    elif entity == "account":
        repository.delete_account(engine, household_id, entity_id)
    elif entity == "budget":
        repository.delete_budget(engine, household_id, entity_id)
    elif entity == "income":
        repository.delete_income_source(engine, household_id, entity_id)
    elif entity == "memory":
        repository.delete_household_memory(engine, household_id, entity_id)
    elif entity == "transaction":
        repository.delete_transaction(engine, household_id, entity_id)
    elif entity == "account_balance":
        repository.delete_account_balance(engine, household_id, entity_id)
    elif entity == "income_profile":
        repository.delete_income_profile(engine, household_id, entity_id)
    elif entity == "member":
        # Removes the membership; the user row survives (harmless, and removal
        # is itself undoable via restore_membership).
        repository.delete_member(engine, household_id, entity_id)
    elif entity == "connection":
        repository.delete_institution_connection(engine, household_id, entity_id)
    elif entity == "goal":
        repository.delete_goal(engine, household_id, entity_id)
    elif entity == "role":
        role = repository.get_role(engine, household_id, entity_id)
        if role is None:
            raise UndoError("that role no longer exists")
        if role.built_in or role.member_count > 0:
            raise UndoError("this role is built-in or still assigned")
        repository.delete_role(engine, household_id, entity_id)
    else:
        raise UndoError("this action can't be undone")


def _recreate(engine: Engine, household_id: str, entity: str | None, data: dict[str, Any]) -> None:
    if entity == "bill":
        repository.create_bill(
            engine, household_id,
            name=data["name"], amount_minor=data["amount_minor"], currency=data["currency"],
            frequency=data["frequency"], next_due_date=_date(data.get("next_due_date")),
            category_id=data.get("category_id"),
        )
    elif entity == "category":
        repository.create_category(engine, household_id, data["name"])
    elif entity == "role":
        if repository.create_role(
            engine, household_id, data["name"], set(data.get("rights") or []),
            role_id=data.get("id"),
        ) is None:
            raise UndoError("a role with that name already exists")
    elif entity == "account":
        repository.create_account(
            engine, household_id,
            name=data["name"], account_type=data["account_type"], currency=data["currency"],
            annual_interest_rate=data.get("annual_interest_rate"),
            minimum_payment_minor=data.get("minimum_payment_minor"),
            maturity_date=_date(data.get("maturity_date")),
        )
    elif entity == "budget":
        repository.create_budget(
            engine, household_id,
            category_id=data["category_id"], limit_minor=data["limit_minor"],
            currency=data["currency"],
        )
    elif entity == "income":
        repository.create_income_source(
            engine, household_id,
            name=data["name"], amount_minor=data["amount_minor"],
            currency=data["currency"], frequency=data["frequency"],
        )
    elif entity == "memory":
        repository.upsert_household_memory(
            engine, household_id, data["key"], data["value"], source=data.get("source", "manual")
        )
    elif entity == "goal":
        repository.create_goal(
            engine, household_id,
            name=data["name"], goal_type=data["goal_type"],
            target_minor=data["target_minor"], currency=data["currency"],
            target_date=_date(data.get("target_date")), priority=data.get("priority", 3),
            monthly_contribution_minor=data.get("monthly_contribution_minor"),
            current_minor=data.get("current_minor", 0),
        )
    elif entity == "income_profile":
        repository.create_income_profile(
            engine, household_id,
            label=data["label"],
            base_salary_minor=data.get("base_salary_minor", 0),
            rsu_annual_minor=data.get("rsu_annual_minor", 0),
            rsu_frequency=data.get("rsu_frequency"),
            rsu_next_vest_date=_date(data.get("rsu_next_vest_date")),
            bonus_percent=data.get("bonus_percent", 0.0),
            bonus_month=data.get("bonus_month"),
            w2_year=data.get("w2_year"),
            w2_wages_minor=data.get("w2_wages_minor"),
            w2_withheld_minor=data.get("w2_withheld_minor"),
        )
    elif entity == "transaction":
        repository.restore_deleted_transaction(
            engine, household_id,
            transaction_id=data["id"], account_id=data["account_id"],
            occurred_at=_date(data["occurred_at"]), amount_minor=data["amount_minor"],
            currency=data["currency"], merchant=data.get("merchant"),
            description=data.get("description"), category_id=data.get("category_id"),
            duplicate_state=data.get("duplicate_state"), external_id=data.get("external_id"),
            note=data.get("note"), attachment_path=data.get("attachment_path"),
            attachment_content_type=data.get("attachment_content_type"),
        )
    else:
        raise UndoError("this action can't be undone")


def _restore(
    engine: Engine, household_id: str, entity: str | None, entity_id: str | None, data: dict[str, Any]
) -> None:
    if not entity_id:
        raise UndoError("this action can't be undone")
    if entity == "role":
        if repository.get_role(engine, household_id, entity_id) is None:
            raise UndoError("that role no longer exists")
        repository.update_role(
            engine, household_id, entity_id,
            name=data.get("name"), role_rights=set(data.get("rights") or []),
        )
        return
    if entity == "bill":
        repository.update_bill(
            engine, household_id, entity_id,
            name=data.get("name"), amount_minor=data.get("amount_minor"),
            currency=data.get("currency"), frequency=data.get("frequency"),
            next_due_date=_date(data.get("next_due_date")), category_id=data.get("category_id"),
        )
    elif entity == "account":
        repository.update_account(
            engine, household_id, entity_id,
            name=data.get("name"), account_type=data.get("account_type"),
            annual_interest_rate=data.get("annual_interest_rate"),
            minimum_payment_minor=data.get("minimum_payment_minor"),
            maturity_date=_date(data.get("maturity_date")),
            emergency_fund_percent=data.get("emergency_fund_percent"),
            emergency_fund_minor=data.get("emergency_fund_minor"),
        )
    elif entity == "budget":
        repository.update_budget_limit(engine, household_id, entity_id, data["limit_minor"])
    elif entity == "category":
        repository.update_category(engine, household_id, entity_id, name=data["name"])
    elif entity == "income":
        repository.update_income_source(
            engine, household_id, entity_id,
            name=data.get("name"), amount_minor=data.get("amount_minor"),
            currency=data.get("currency"), frequency=data.get("frequency"),
        )
    elif entity == "tax_settings":
        repository.update_tax_settings(
            engine, household_id,
            tax_filing_status=data.get("tax_filing_status"),
            income_treated_as_net=data.get("income_treated_as_net"),
            state=data.get("state"),
        )
    elif entity == "household_settings":
        repository.update_emergency_fund_target(
            engine, household_id, data.get("emergency_fund_target_months")
        )
        repository.set_credit_cards_paid_in_full(
            engine, household_id, bool(data.get("credit_cards_paid_in_full", False))
        )
    elif entity == "member_role":
        if not repository.update_member_role(
            engine, household_id, entity_id, data.get("role", "viewer")
        ):
            raise UndoError("the member no longer exists")
    elif entity == "goal":
        if not repository.update_goal(
            engine, household_id, entity_id,
            name=data.get("name"),
            target_minor=data.get("target_minor"),
            target_date=_date(data.get("target_date")),
            priority=data.get("priority"),
            monthly_contribution_minor=data.get("monthly_contribution_minor"),
        ):
            raise UndoError("the goal no longer exists")
    elif entity == "ai_runtime":
        repository.upsert_ai_runtime_config(
            engine, household_id,
            provider=data["provider"], base_url=data["base_url"],
            model=data["model"], enabled=bool(data.get("enabled", False)),
        )
    elif entity == "transaction":
        if repository.get_transaction(engine, household_id, entity_id) is None:
            raise UndoError("the transaction no longer exists")
        # Restore every mutable field to its prior value. merchant/description/
        # account/date/amount go through update_transaction; note and duplicate
        # flag have their own setters that also handle clearing to NULL; the
        # category is set, or cleared when it was previously empty.
        repository.update_transaction(
            engine, household_id, entity_id,
            account_id=data.get("account_id"), occurred_at=_date(data.get("occurred_at")),
            amount_minor=data.get("amount_minor"), currency=data.get("currency"),
            merchant=data.get("merchant"), description=data.get("description"),
            category_id=data.get("category_id"), clear_category=data.get("category_id") is None,
        )
        repository.set_transaction_note(engine, household_id, entity_id, data.get("note"))
        repository.set_transaction_duplicate_state(
            engine, household_id, entity_id, data.get("duplicate_state")
        )
        if "attachment_path" in data:
            repository.set_transaction_attachment(
                engine, household_id, entity_id,
                data.get("attachment_path"), data.get("attachment_content_type"),
            )
    else:
        raise UndoError("this action can't be undone")
