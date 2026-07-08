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

from family_cfo_api import repository


def write_audit(
    engine: Engine,
    household_id: str,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    summary: str,
) -> None:
    repository.record_audit_event(
        engine,
        household_id=household_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
    )
