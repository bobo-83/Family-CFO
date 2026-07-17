"""ADR 0023: the undo-completeness rule is guarded here.

Every audit action the API emits must be classified in ``undo_actions.UNDO_POLICY``
(so ``audit.write_audit`` accepts it), and the set of actions that are merely
PENDING (should be undoable, not yet wired) may only shrink — a new mutation
cannot quietly ship without undo.
"""

import ast
import pathlib

import pytest

from family_cfo_api import audit, undo_actions

_SRC = pathlib.Path(undo_actions.__file__).parent


def _emitted_actions() -> set[str]:
    """Every string passed as the 4th positional arg to ``write_audit`` anywhere
    in the package — i.e. every audit action the code actually emits."""
    actions: set[str] = set()
    for path in _SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "write_audit" and len(node.args) >= 4:
                action = node.args[3]
                if isinstance(action, ast.Constant) and isinstance(action.value, str):
                    actions.add(action.value)
    return actions


# The undo debt was fully drained in M117: every state-changing action is now
# UNDOABLE; the only IRREVERSIBLE entries are genuine external side effects
# (logins, NAS snapshots, revealed secrets, an operational model swap, a bulk
# delete of staged rows re-doable by re-upload). This set must stay empty.
_FROZEN_PENDING: set[str] = set()


def test_every_emitted_audit_action_is_classified() -> None:
    missing = sorted(a for a in _emitted_actions() if a not in undo_actions.UNDO_POLICY)
    assert not missing, (
        f"these audit actions have no undo policy: {missing}. Classify each in "
        "undo_actions.UNDO_POLICY (UNDOABLE / IRREVERSIBLE / PENDING) — see ADR 0023."
    )


def test_pending_undo_debt_only_shrinks() -> None:
    pending = {a for a, p in undo_actions.UNDO_POLICY.items() if p == undo_actions.PENDING}
    grew = sorted(pending - _FROZEN_PENDING)
    assert not grew, (
        f"PENDING (non-undoable) actions were introduced: {grew}. The PENDING set was "
        "drained in M117 and must stay empty — wire an undo token (ADR 0023)."
    )


def test_write_audit_rejects_an_unclassified_action(demo_engine) -> None:
    with pytest.raises(ValueError, match="no undo policy"):
        audit.write_audit(
            demo_engine, "hh", None, "totally.new_action", "thing", "id", "did a thing"
        )


def test_write_audit_requires_a_token_for_undoable_actions(demo_engine) -> None:
    with pytest.raises(ValueError, match="UNDOABLE but no undo_token"):
        audit.write_audit(
            demo_engine, "hh", None, "bill.deleted", "bill", "id", "Deleted a bill"
        )
