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

ADR 0073 defines what a token has to contain: an undo restores the state as it
was immediately before the action, so a token carries prior VALUES (never an
inverse instruction), and its footprint is everything the action changed,
cascades included. Two rules follow, and both are load-bearing here:

- A write that can UPDATE an existing row must not mint a ``delete`` token.
- A snapshot that cannot fit in one audit row is refused, not truncated: the
  builder returns :func:`irreversible` and the event honestly reports that it
  cannot be undone (#71).
"""

from __future__ import annotations

import json
from datetime import date, datetime
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
    "savings_contribution.created": UNDOABLE,
    "savings_contribution.deleted": UNDOABLE,
    "savings_contribution.dismissed": UNDOABLE,
    "card_statement.recorded": UNDOABLE,
    # #25: reading a statement's lines REPLACES the previous read, and the point
    # of the feature is that the newest read is the truth. "Undo" would restore
    # a worse scan; deleting the statement removes the lines with it.
    "statement_lines.recorded": IRREVERSIBLE,
    "card_statement.deleted": UNDOABLE,
    "card_statement.paid": UNDOABLE,
    "card_statement.unpaid": UNDOABLE,
    "savings_contribution.linked": UNDOABLE,
    "savings_contribution.unlinked": UNDOABLE,
    "bill.payment_linked": UNDOABLE,
    "bill.payment_unlinked": UNDOABLE,
    "bill.updated": UNDOABLE,
    "bill.deleted": UNDOABLE,
    "bill_suggestion.dismissed": UNDOABLE,
    "bill_credit.recorded": UNDOABLE,
    # RSU grants (M-rsu-grants)
    "rsu_grant.created": UNDOABLE,
    "rsu_grant.deleted": UNDOABLE,
    "rsu_vest_event.created": UNDOABLE,
    "rsu_vest_event.updated": UNDOABLE,
    "rsu_vest_event.deleted": UNDOABLE,
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
    # #63: a sync mirrors what the institution reports — balances recorded and
    # transactions inserted. "Undoing" it would delete provider-sourced rows that
    # the very next sync re-imports (the ADR 0015 external_id dedupe compares
    # against what is stored, so a deletion is not remembered), i.e. the undo does
    # not hold. Delete the transactions, or unlink the connection, instead.
    "connection.synced": IRREVERSIBLE,
    # #63: auto-filing only ever fills a BLANK category, so the exact inverse is
    # to blank those same transactions again — the token carries their ids.
    "transactions.auto_filed": UNDOABLE,
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
    "household.recovery_key_generated": IRREVERSIBLE,  # old key invalidated, new one shown
    "household.seal_mode_changed": IRREVERSIBLE,  # reversible via the toggle, not via undo
    "household.recovery_key_used": IRREVERSIBLE,  # an unlock happened; nothing to reverse
    "household.hosted_created": IRREVERSIBLE,  # revoke the invite to abort; shells are inert
    "household.hosted_deleted": IRREVERSIBLE,  # the point of the confirmation
    "household.exported": IRREVERSIBLE,  # data left the box; can't be unshared
    "household.key_rotated": IRREVERSIBLE,  # key material replaced; nothing to restore
    "backup.config_updated": IRREVERSIBLE,  # holds credentials we never store for replay
    "backup_job": IRREVERSIBLE,  # a scheduled backup ran
    # auth & pairing
    "auth.login": IRREVERSIBLE,  # a sign-in isn't a change to reverse
    # #97: an undo token would have to carry the OLD password hash — a stored
    # credential, which this codebase refuses (cf. invite.accepted). And undo
    # would re-mint the key wrap for the very password the member is retiring,
    # so the "undo" would defeat the action. Change it again to change it back.
    "auth.password_changed": IRREVERSIBLE,
    "pairing.confirmed": IRREVERSIBLE,  # a device was paired (revoke it from Devices)
    "pairing.device_revoked": IRREVERSIBLE,  # a device was revoked (re-pair to restore)
    "pairing.login": IRREVERSIBLE,  # a device signed in (revoke it from Devices)
    # invites (ADR 0056)
    "invite.created": UNDOABLE,
    "invite.revoked": UNDOABLE,
    # ADR 0065: box-admin roster changes. Both directions restore cleanly; the
    # never-remove-the-last-admin rule is enforced on the undo path too.
    "system_admin.granted": UNDOABLE,
    "system_admin.revoked": UNDOABLE,
    "invite.token_regenerated": IRREVERSIBLE,  # revealed a new secret; the old one isn't stored
    "invite.accepted": IRREVERSIBLE,  # the invitee set a password we refuse to replay
    # goals
    "goal.created": UNDOABLE,
    "goal.updated": UNDOABLE,
    "goal.deleted": UNDOABLE,
    # reports
    "report.generated": IRREVERSIBLE,  # a document was produced; nothing to undo
    # #61: the undo itself is an event too, so the timeline reads top to bottom.
    # Undoing an undo is a redo: the honest answer is to perform the action
    # again, not to nest reversals — so this one is IRREVERSIBLE by design.
    "audit.reverted": IRREVERSIBLE,
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


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# ADR 0073 + #71: a snapshot has to fit in one `audit_events.undo_token` column.
# Any action whose footprint scales with household data — a delete that cascades,
# a bulk auto-file — gets an honest IRREVERSIBLE token past this many rows,
# instead of a multi-hundred-kilobyte blob or, worse, a partial one.
SNAPSHOT_ROW_LIMIT = 500


# --- token builders (called by the write handlers) --------------------------


def created(entity: str, entity_id: str) -> str:
    """A CREATE is undone by deleting the new record."""
    return json.dumps({"op": "delete", "entity": entity, "id": entity_id})


def irreversible(reason: str) -> str:
    """A token that says, in the row itself, that this particular event cannot be
    put back (ADR 0073).

    The action stays UNDOABLE in ``UNDO_POLICY`` — most of its events are — but
    this one exceeded what a snapshot can carry, so the Activity screen offers
    no Undo for it and the reason is on the record.
    """
    return json.dumps({"op": "irreversible", "reason": reason})


def token_is_reversible(raw: str | None) -> bool:
    """Whether an audit row's stored token can actually be reversed — used to
    decide the ``undoable`` flag, so the UI never offers an Undo that errors."""
    if raw is None:
        return False
    try:
        token = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(token, dict) and token.get("op") != "irreversible"


def system_admin_revoked(user_id: str, granted_by_user_id: str | None) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "system_admin",
            "data": {"user_id": user_id, "granted_by_user_id": granted_by_user_id},
        }
    )


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


def rsu_grant_deleted(
    grant: repository.RsuGrantRecord, events: list[repository.RsuVestEventRecord]
) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "rsu_grant",
            "data": {
                "income_profile_id": grant.income_profile_id,
                "ticker": grant.ticker,
                "units": grant.units,
                "grant_date": _iso(grant.grant_date),
                "vest_years": grant.vest_years,
                "frequency": grant.frequency,
                # The schedule as it stood (edits included), not a re-derivation.
                "events": [
                    {"vest_date": _iso(e.vest_date), "units": e.units} for e in events
                ],
            },
        }
    )


def rsu_vest_event_updated(before: repository.RsuVestEventRecord) -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "rsu_vest_event",
            "id": before.id,
            "data": {"vest_date": _iso(before.vest_date), "units": before.units},
        }
    )


def rsu_vest_event_deleted(before: repository.RsuVestEventRecord) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "rsu_vest_event",
            "data": {
                "grant_id": before.grant_id,
                "vest_date": _iso(before.vest_date),
                "units": before.units,
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


def role_updated(before: repository.RoleRecord) -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "role",
            "id": before.id,
            "data": {"name": before.name, "rights": sorted(before.rights)},
        }
    )


def role_deleted(before: repository.RoleRecord) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "role",
            "data": {"id": before.id, "name": before.name, "rights": sorted(before.rights)},
        }
    )


def category_deleted(
    category: repository.CategoryRecord,
    transaction_ids: list[str],
    bill_ids: list[str],
    budget: repository.BudgetRecord | None,
) -> str:
    """#72: deleting a category is four changes, not one — the category row, the
    ``category_id`` nulled on every transaction filed under it, the same nulled
    on every bill filed under it (#76), and the budget envelope deleted with it.
    The token carries all four, and the category comes back under its ORIGINAL
    id so the transactions, the bills and the envelope point at the same
    category they did before.

    ``transaction_ids`` and ``bill_ids`` are each read with
    :data:`SNAPSHOT_ROW_LIMIT` + 1 rows: one over the limit means the household
    files more rows here than a single audit row can hold, and the honest answer
    is IRREVERSIBLE (#71).
    """
    if len(transaction_ids) > SNAPSHOT_ROW_LIMIT:
        return irreversible(
            f"more than {SNAPSHOT_ROW_LIMIT} transactions were filed under this "
            "category; their categories cannot be restored"
        )
    if len(bill_ids) > SNAPSHOT_ROW_LIMIT:
        return irreversible(
            f"more than {SNAPSHOT_ROW_LIMIT} bills were filed under this "
            "category; their categories cannot be restored"
        )
    return json.dumps(
        {
            "op": "recreate",
            "entity": "category",
            "data": {
                "id": category.id,
                "name": category.name,
                "transaction_ids": transaction_ids,
                "bill_ids": bill_ids,
                "budget": (
                    {
                        "id": budget.id,
                        "limit_minor": budget.limit_minor,
                        "currency": budget.currency,
                    }
                    if budget is not None
                    else None
                ),
            },
        }
    )


def category_updated(before: repository.CategoryRecord) -> str:
    return json.dumps(
        {"op": "restore", "entity": "category", "id": before.id, "data": {"name": before.name}}
    )


def account_deleted(
    account: repository.AccountRecord, balances: list[tuple[str, int, datetime]]
) -> str:
    """#72: ``delete_account`` drops the account's balance history with it, so the
    token carries the history and the account's own id — the account comes back
    where its statements, savings routes and snapshots still point.

    ``balances`` is read with :data:`SNAPSHOT_ROW_LIMIT` + 1 rows; one over the
    limit means the history is longer than an audit row can hold (#71).
    """
    if len(balances) > SNAPSHOT_ROW_LIMIT:
        return irreversible(
            f"this account has more than {SNAPSHOT_ROW_LIMIT} recorded balances; "
            "its history cannot be restored"
        )
    return json.dumps(
        {
            "op": "recreate",
            "entity": "account",
            "data": {
                "id": account.id,
                "name": account.name,
                "account_type": account.account_type,
                "currency": account.currency,
                "annual_interest_rate": account.annual_interest_rate,
                "minimum_payment_minor": account.minimum_payment_minor,
                "maturity_date": _iso(account.maturity_date),
                "next_payment_due_date": _iso(account.next_payment_due_date),
                "emergency_fund_percent": account.emergency_fund_percent,
                "emergency_fund_minor": account.emergency_fund_minor,
                "rsu_ready_to_sell": account.rsu_ready_to_sell,
                "balances": [
                    {"id": bid, "balance_minor": minor, "as_of": as_of.isoformat()}
                    for bid, minor, as_of in balances
                ],
            },
        }
    )


def account_updated(before: repository.AccountRecord) -> str:
    """#72: every column the PATCH can write, ``next_payment_due_date`` included.

    Restored through ``repository.restore_account``, not ``update_account``:
    a PATCH reads None as "leave unchanged", which would make ADDING a value
    (a rate, a due date, an emergency-fund designation) impossible to undo.
    """
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
                "next_payment_due_date": _iso(before.next_payment_due_date),
                "emergency_fund_percent": before.emergency_fund_percent,
                "emergency_fund_minor": before.emergency_fund_minor,
                "rsu_ready_to_sell": before.rsu_ready_to_sell,
            },
        }
    )


def bill_payment_unlinked(link: repository.BillPaymentLinkRecord) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "bill_payment_link",
            "data": {
                "id": link.id,
                "bill_id": link.bill_id,
                "transaction_id": link.transaction_id,
                "due_date": _iso(link.due_date),
            },
        }
    )


def savings_contribution_deleted(record) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "savings_contribution",
            "data": {
                "id": record.id,
                "source_account_id": record.source_account_id,
                "destination_account_id": record.destination_account_id,
                "amount_minor": record.amount_minor,
                "currency": record.currency,
                "frequency": record.frequency,
                "source": record.source,
                "label_key": record.label_key,
            },
        }
    )


def card_statement_paid_changed(record) -> str:
    """#11: undo restores the PREVIOUS paid mark (which may be None)."""
    return json.dumps(
        {
            "op": "card_statement_paid",
            "statement_id": record.id,
            "paid_at": record.paid_at.isoformat() if record.paid_at else None,
        }
    )


def card_statement_recorded(record, before) -> str:
    """#72: recording a cycle is an UPSERT, so its undo depends on what the write
    actually did.

    A first record for the cycle created the row, and deleting it is the whole
    reversal. A second record for the same account and due date CORRECTED the
    figures in place — minting a delete token there would destroy the statement
    and the original figures both, which is the data loss the Undo button exists
    to prevent. ``before`` is what the upsert overwrote, or None if it created.
    """
    if before is None:
        return created("card_statement", record.id)
    return json.dumps(
        {
            "op": "restore",
            "entity": "card_statement",
            "id": before.id,
            "data": {
                "statement_balance_minor": before.statement_balance_minor,
                "minimum_due_minor": before.minimum_due_minor,
                "currency": before.currency,
                "period_start": _iso(before.period_start),
                "period_end": _iso(before.period_end),
                "document_id": before.document_id,
            },
        }
    )


def card_statement_deleted(record) -> str:
    return json.dumps(
        {
            "op": "recreate",
            "entity": "card_statement",
            "data": {
                "id": record.id,
                "account_id": record.account_id,
                "statement_balance_minor": record.statement_balance_minor,
                "minimum_due_minor": record.minimum_due_minor,
                "currency": record.currency,
                "due_date": record.due_date.isoformat(),
                "period_start": record.period_start.isoformat() if record.period_start else None,
                "period_end": record.period_end.isoformat() if record.period_end else None,
                "document_id": record.document_id,
                "paid_at": record.paid_at.isoformat() if record.paid_at else None,
            },
        }
    )


def savings_contribution_link_changed(record) -> str:
    """Undo restores the PREVIOUS goal link (which may be None)."""
    return json.dumps(
        {
            "op": "relink_savings_contribution",
            "contribution_id": record.id,
            "goal_id": record.goal_id,
        }
    )


def savings_route_dismissed(source_account_id: str, destination_account_id: str) -> str:
    return json.dumps(
        {
            "op": "undismiss_savings_route",
            "source_account_id": source_account_id,
            "destination_account_id": destination_account_id,
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


def transaction_updated(before: repository.TransactionRecord) -> str:
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


def transaction_deleted(before: repository.TransactionRecord) -> str:
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


def goal_updated(before: repository.GoalRecord) -> str:
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


def goal_deleted(goal: repository.GoalRecord) -> str:
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


def transactions_auto_filed(transaction_ids: list[str]) -> str:
    """#63: auto-filing (transfers/income/taxes/known merchants) only assigns a
    category to transactions that had none, so the inverse is to clear the category
    on exactly those ids. One token for the whole run — an audit row per operation,
    not per transaction.

    #71: nothing bounded that list. A first sync of several years of history
    auto-files tens of thousands of rows, and every id went into one
    ``audit_events.undo_token`` column — written on every run and read back
    whenever the Activity screen renders the event. Above
    :data:`SNAPSHOT_ROW_LIMIT` the token says so honestly instead (ADR 0073),
    the same answer :func:`category_deleted` and :func:`account_deleted` give.

    The bound does NOT vary by caller, though auto-file runs both from a user's
    sync and from the nightly worker with a null actor (#63). Two reasons. The
    undo endpoint gates on the AUDIT_VIEW right, not on the actor, so a
    null-actor row is listed on the Activity screen and any member can undo it —
    "nobody can undo it anyway" is not true of the worker path. And the thing
    being bounded is the size of one audit row, which is a property of the row,
    not of who caused it; a per-path bound would make the same 600-row run
    undoable after a manual sync and not after a nightly one, which is arbitrary
    to the person reading the log. The size difference the two paths actually
    have — a nightly 20 rows versus a first sync of 20,000 — is already handled
    by a single bound, because only one of them comes near it.
    """
    if len(transaction_ids) > SNAPSHOT_ROW_LIMIT:
        return irreversible(
            f"this run auto-filed more than {SNAPSHOT_ROW_LIMIT} transactions; "
            "their previous categories cannot be restored"
        )
    return json.dumps({"op": "unfile_categories", "ids": list(transaction_ids)})


def income_override_set(transaction_id: str, previous_verdict: str | None) -> str:
    """Restore the previous include/exclude verdict, or clear it (M117)."""
    return json.dumps(
        {
            "op": "income_override",
            "transaction_id": transaction_id,
            "previous_verdict": previous_verdict,
        }
    )


def income_profile_deleted(
    profile: repository.IncomeProfileRecord,
    grants: list[repository.RsuGrantRecord],
    events: list[repository.RsuVestEventRecord],
) -> str:
    """#72: removing an earner cascade-deletes every RSU grant and vest event
    they own, so the token carries the whole schedule as it stood — and the
    earner's own id, because the grants are restored pointing back at it.

    Bounded by construction: grants and their vest schedules are hand-entered
    per earner, tens of rows at the outside, not the thousands #71 is about.
    """
    events_by_grant: dict[str, list[repository.RsuVestEventRecord]] = {}
    for event in events:
        events_by_grant.setdefault(event.grant_id, []).append(event)
    return json.dumps(
        {
            "op": "recreate",
            "entity": "income_profile",
            "data": {
                "id": profile.id,
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
                # #6: pre-tax payroll deductions were dropped by the old token too.
                "retirement_contribution_annual_minor": (
                    profile.retirement_contribution_annual_minor
                ),
                "hsa_contribution_annual_minor": profile.hsa_contribution_annual_minor,
                "grants": [
                    {
                        "id": grant.id,
                        "ticker": grant.ticker,
                        "units": grant.units,
                        "grant_date": _iso(grant.grant_date),
                        "vest_years": grant.vest_years,
                        "frequency": grant.frequency,
                        # The schedule as it stood, edits included.
                        "events": [
                            {"vest_date": _iso(e.vest_date), "units": e.units}
                            for e in events_by_grant.get(grant.id, [])
                        ],
                    }
                    for grant in grants
                ],
            },
        }
    )


def tax_settings_updated(before: repository.HouseholdRecord) -> str:
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


def household_updated(before: repository.HouseholdRecord) -> str:
    return json.dumps(
        {
            "op": "restore",
            "entity": "household_settings",
            "id": before.id,
            "data": {
                "emergency_fund_target_months": before.emergency_fund_target_months,
                "credit_cards_paid_in_full": before.credit_cards_paid_in_full,
                # #43: clearing the zone is now reachable, so undo has to be
                # able to put the old one back. Null is a real value here.
                "timezone": before.timezone,
                # #72: the PATCH writes these two as well, and both were audited
                # as changed while the undo silently left them alone.
                "reserve_committed_savings": before.reserve_committed_savings,
                "language": before.language,
            },
        }
    )


def member_removed(user_id: str, role: str, email: str | None = None) -> str:
    """The user row survives removal; re-inserting the membership restores access.

    #60: removal also ARCHIVES the login address, so the token carries the
    original — restoring the membership without it leaves a member who is
    listed but cannot sign in, which looks like a successful undo and is not.
    """
    return json.dumps(
        {"op": "restore_membership", "user_id": user_id, "role": role, "email": email}
    )


def invite_revoked(invite_id: str) -> str:
    """The token hash survives revocation, so clearing revoked_at makes the
    original link work again (ADR 0056)."""
    return json.dumps({"op": "unrevoke_invite", "id": invite_id})


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

    if op == "irreversible":
        # The builder refused to snapshot this one (ADR 0073): say why, rather
        # than restoring the part that happened to fit.
        raise UndoError(token.get("reason") or "this action can't be undone")

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

    if op == "unfile_categories":
        # #63: reverse of a bulk auto-file — every listed transaction was
        # uncategorized before the run, so blanking them restores the prior state.
        # Rows deleted since are simply skipped; the rest still revert.
        ids = [str(i) for i in (token.get("ids") or [])]
        if not ids:
            raise UndoError("this action can't be undone")
        repository.clear_transactions_category(engine, household_id, ids)
        return

    if op == "undismiss_suggestion":
        key = token.get("merchant_key")
        if not key:
            raise UndoError("this action can't be undone")
        repository.remove_bill_suggestion_dismissal(engine, household_id, key)
        return

    if op == "undismiss_savings_route":
        # #203 latent fix: the token was written but never dispatched, so
        # undoing a "not saving" dismissal errored. Found while adding #4.
        source = token.get("source_account_id")
        destination = token.get("destination_account_id")
        if not source or not destination:
            raise UndoError("this action can't be undone")
        repository.undismiss_savings_route(engine, household_id, source, destination)
        return

    if op == "card_statement_paid":
        statement_id = token.get("statement_id")
        if not statement_id:
            raise UndoError("this action can't be undone")
        raw = token.get("paid_at")
        repository.set_card_statement_paid(
            engine, household_id, statement_id,
            date.fromisoformat(raw) if raw else None,
        )
        return

    if op == "relink_savings_contribution":
        contribution_id = token.get("contribution_id")
        if not contribution_id:
            raise UndoError("this action can't be undone")
        repository.link_savings_contribution_to_goal(
            engine, household_id, contribution_id, token.get("goal_id")
        )
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
            engine,
            household_id,
            user_id,
            token.get("role") or "viewer",
            # Absent in tokens minted before #60 — restore what we can.
            token.get("email"),
        )
        return

    if op == "ai_runtime_clear":
        repository.delete_ai_runtime_config(engine, household_id)
        return

    if op == "unrevoke_invite":
        invite_id = token.get("id")
        if not invite_id or not repository.unrevoke_invite(engine, household_id, invite_id):
            raise UndoError("that invite no longer exists or was accepted")
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
    elif entity == "bill_credit":
        repository.delete_bill_credit(engine, household_id, entity_id)
    elif entity == "savings_contribution":
        repository.delete_savings_contribution(engine, household_id, entity_id)
    elif entity == "card_statement":
        repository.delete_card_statement(engine, household_id, entity_id)
    elif entity == "bill_payment_link":
        repository.delete_bill_payment_link(engine, household_id, entity_id)
    elif entity == "rsu_grant":
        repository.delete_rsu_grant(engine, household_id, entity_id)
    elif entity == "rsu_vest_event":
        repository.delete_rsu_vest_event(engine, household_id, entity_id)
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
        # Removes the membership and archives the login address (#60); the
        # user row survives because NOT NULL references depend on it, and the
        # removal is itself undoable via restore_membership.
        repository.delete_member(engine, household_id, entity_id)
    elif entity == "invite":
        # Undo of invite.created: remove the invite outright — the link dies.
        if not repository.delete_invite(engine, household_id, entity_id):
            raise UndoError("that invite was already accepted")
    elif entity == "system_admin":
        # Undo of system_admin.granted (entity_id is the USER id). The roster
        # must never empty — same rule as the delete endpoint.
        if repository.count_system_admins(engine) <= 1:
            raise UndoError("the box must keep at least one system administrator")
        if not repository.revoke_system_admin(engine, entity_id):
            raise UndoError("that user is no longer a system administrator")
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
    if entity == "system_admin":
        # Undo of system_admin.revoked: put the user back on the roster.
        repository.grant_system_admin(
            engine, data["user_id"], data.get("granted_by_user_id")
        )
        return
    if entity == "bill":
        repository.create_bill(
            engine, household_id,
            name=data["name"], amount_minor=data["amount_minor"], currency=data["currency"],
            frequency=data["frequency"], next_due_date=_date(data.get("next_due_date")),
            category_id=data.get("category_id"),
        )
    elif entity == "rsu_grant":
        vest_date_of = _date  # readability below
        repository.create_rsu_grant(
            engine, household_id,
            income_profile_id=data["income_profile_id"], ticker=data["ticker"],
            units=data["units"], grant_date=_date(data["grant_date"]),
            vest_years=data["vest_years"], frequency=data["frequency"],
            events=[
                (vest_date_of(e["vest_date"]), e["units"]) for e in data.get("events") or []
            ],
        )
    elif entity == "rsu_vest_event":
        repository.add_rsu_vest_event(
            engine, household_id, data["grant_id"], _date(data["vest_date"]), data["units"]
        )
    elif entity == "category":
        # #72/#76: the whole footprint of a category delete — the row under its
        # ORIGINAL id, the transactions and the bills whose category it nulled,
        # and the budget envelope it deleted. Tokens minted before #72 carry only
        # the name, so they still recreate under a fresh id and restore nothing
        # else; tokens minted between #72 and #76 have no `bill_ids`.
        category = repository.create_category(
            engine, household_id, data["name"], category_id=data.get("id")
        )
        transaction_ids = [str(i) for i in (data.get("transaction_ids") or [])]
        if transaction_ids:
            repository.set_transactions_category(
                engine, household_id, transaction_ids, category.id
            )
        bill_ids = [str(i) for i in (data.get("bill_ids") or [])]
        if bill_ids:
            repository.set_bills_category(engine, household_id, bill_ids, category.id)
        budget = data.get("budget")
        if budget:
            repository.create_budget(
                engine, household_id,
                category_id=category.id, limit_minor=budget["limit_minor"],
                currency=budget["currency"], budget_id=budget.get("id"),
            )
    elif entity == "role":
        if repository.create_role(
            engine, household_id, data["name"], set(data.get("rights") or []),
            role_id=data.get("id"),
        ) is None:
            raise UndoError("a role with that name already exists")
    elif entity == "account":
        # #72: back under its original id, with the emergency-fund designation,
        # the RSU tag and the balance history the delete took with it.
        account = repository.create_account(
            engine, household_id,
            name=data["name"], account_type=data["account_type"], currency=data["currency"],
            annual_interest_rate=data.get("annual_interest_rate"),
            minimum_payment_minor=data.get("minimum_payment_minor"),
            maturity_date=_date(data.get("maturity_date")),
            next_payment_due_date=_date(data.get("next_payment_due_date")),
            emergency_fund_percent=data.get("emergency_fund_percent"),
            emergency_fund_minor=data.get("emergency_fund_minor"),
            rsu_ready_to_sell=bool(data.get("rsu_ready_to_sell", False)),
            account_id=data.get("id"),
        )
        for balance in data.get("balances") or []:
            repository.record_account_balance(
                engine,
                account.id,
                balance["balance_minor"],
                as_of=_datetime(balance.get("as_of")),
                balance_id=balance.get("id"),
            )
    elif entity == "card_statement":
        repository.upsert_card_statement(
            engine, household_id,
            account_id=data["account_id"],
            statement_balance_minor=data["statement_balance_minor"],
            minimum_due_minor=data.get("minimum_due_minor"),
            currency=data["currency"],
            due_date=date.fromisoformat(data["due_date"]),
            period_start=(
                date.fromisoformat(data["period_start"]) if data.get("period_start") else None
            ),
            period_end=(
                date.fromisoformat(data["period_end"]) if data.get("period_end") else None
            ),
            document_id=data.get("document_id"),
            statement_id=data.get("id"),
        )
    elif entity == "savings_contribution":
        repository.create_savings_contribution(
            engine, household_id,
            source_account_id=data["source_account_id"],
            destination_account_id=data["destination_account_id"],
            amount_minor=data["amount_minor"], currency=data["currency"],
            frequency=data["frequency"], source=data["source"],
            label_key=data.get("label_key"), contribution_id=data.get("id"),
        )
    elif entity == "bill_payment_link":
        repository.create_bill_payment_link(
            engine, household_id,
            bill_id=data["bill_id"], transaction_id=data["transaction_id"],
            due_date=_date(data["due_date"]), link_id=data.get("id"),
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
        # #72: the earner comes back under their ORIGINAL id, then every RSU
        # grant the delete cascaded through, with the vest schedule as it stood.
        # Tokens minted before #72 carry neither, and restore the profile alone.
        profile_id = repository.create_income_profile(
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
            retirement_contribution_annual_minor=data.get(
                "retirement_contribution_annual_minor", 0
            ),
            hsa_contribution_annual_minor=data.get("hsa_contribution_annual_minor", 0),
            profile_id=data.get("id"),
        )
        for grant in data.get("grants") or []:
            repository.create_rsu_grant(
                engine, household_id,
                income_profile_id=profile_id, ticker=grant["ticker"],
                units=grant["units"], grant_date=_date(grant["grant_date"]),
                vest_years=grant["vest_years"], frequency=grant["frequency"],
                events=[
                    (_date(e["vest_date"]), e["units"]) for e in grant.get("events") or []
                ],
                grant_id=grant.get("id"),
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
    if entity == "rsu_vest_event":
        repository.update_rsu_vest_event(
            engine, household_id, entity_id,
            vest_date=_date(data.get("vest_date")), units=data.get("units"),
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
        # #72: written straight back, nulls included — a PATCH-style update
        # would read None as "leave unchanged" and quietly fail to reverse an
        # ADDED rate, due date or emergency-fund designation. Presence, not
        # truthiness (as #43 did for the timezone): a key the token never
        # captured keeps whatever the account has now, so tokens minted before
        # #72 don't clear a column they said nothing about.
        current = repository.get_account(engine, household_id, entity_id)
        if current is None:
            raise UndoError("that account no longer exists")

        repository.restore_account(
            engine, household_id, entity_id,
            name=data.get("name", current.name),
            account_type=data.get("account_type", current.account_type),
            annual_interest_rate=data.get(
                "annual_interest_rate", current.annual_interest_rate
            ),
            minimum_payment_minor=data.get(
                "minimum_payment_minor", current.minimum_payment_minor
            ),
            maturity_date=(
                _date(data["maturity_date"])
                if "maturity_date" in data
                else current.maturity_date
            ),
            next_payment_due_date=(
                _date(data["next_payment_due_date"])
                if "next_payment_due_date" in data
                else current.next_payment_due_date
            ),
            emergency_fund_percent=data.get(
                "emergency_fund_percent", current.emergency_fund_percent
            ),
            emergency_fund_minor=data.get(
                "emergency_fund_minor", current.emergency_fund_minor
            ),
            rsu_ready_to_sell=bool(
                data.get("rsu_ready_to_sell", current.rsu_ready_to_sell)
            ),
        )
    elif entity == "card_statement":
        # #72: undo of a CORRECTION to a cycle — put the overwritten figures
        # back. The statement itself was not created by that write, so it stays.
        if not repository.restore_card_statement(
            engine, household_id, entity_id,
            statement_balance_minor=data["statement_balance_minor"],
            minimum_due_minor=data.get("minimum_due_minor"),
            currency=data["currency"],
            period_start=_date(data.get("period_start")),
            period_end=_date(data.get("period_end")),
            document_id=data.get("document_id"),
        ):
            raise UndoError("that statement no longer exists")
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
        # #43: presence, not truthiness — null means "it was on the box's own
        # zone". Tokens minted before #43 carry no key at all, and restoring
        # None for those would clear a zone the update never touched.
        if "timezone" in data:
            repository.set_household_timezone(engine, household_id, data["timezone"])
        # #72: the same PATCH writes these two, and neither was restored. Same
        # presence rule — null language means "never chose one", not "no change".
        if "reserve_committed_savings" in data:
            repository.set_reserve_committed_savings(
                engine, household_id, bool(data["reserve_committed_savings"])
            )
        if "language" in data:
            repository.set_household_language(engine, household_id, data["language"])
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
