"""Audit-log helper for sensitive mutations.

Every write path in M9 calls ``write_audit`` after the mutation succeeds. The
``summary`` must never contain ``Restricted``/``Sensitive`` values (amounts,
balances, passwords, tokens) -- only ids, action names, and non-sensitive
labels like an account name or member email. This mirrors the logging
conventions established in M3 (purchase details), M4 (prompts), and M7 (file
contents): audit rows are ``Internal`` per the security model.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from family_cfo_api import repository, undo_actions


def write_audit(
    engine: Engine,
    household_id: str,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    summary: str,
    undo_token: str | None = None,
) -> str:
    """Record an audit row. ``undo_token`` (JSON) makes the action reversible from
    the Activity/History screen (M101); leave it None for actions that can't be
    undone. Returns the new row id.

    Enforces the undo-completeness rule (ADR 0023): the action must be classified
    in ``undo_actions.UNDO_POLICY``, and an action declared UNDOABLE must carry a
    token. A new mutation therefore can't ship without a deliberate undo decision.
    """
    policy = undo_actions.require_classified(action)
    if policy == undo_actions.UNDOABLE and undo_token is None:
        raise ValueError(
            f"audit action {action!r} is classified UNDOABLE but no undo_token was "
            "provided — pass one, or reclassify it in undo_actions.UNDO_POLICY (ADR 0023)."
        )
    return repository.record_audit_event(
        engine,
        household_id=household_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        undo_token=undo_token,
    )
