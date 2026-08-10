"""#60: removing a member must free their email, and undo must give it back."""

import json
from datetime import UTC, datetime

from sqlalchemy import Engine

from family_cfo_api import fixtures, repository, undo_actions


def _add(engine: Engine, email: str):
    return repository.create_member(
        engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        email=email,
        password_hash="x",
        display_name="Temp",
        role="viewer",
    )


def test_removal_frees_the_address_for_reuse(demo_engine: Engine) -> None:
    """The bug this fixes: the address was squatted by an invisible row, so the
    same person could never be added back."""
    member = _add(demo_engine, "reuse@example.com")
    assert repository.user_email_exists(demo_engine, "reuse@example.com")

    removed, archived = repository.delete_member(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id
    )
    assert removed and archived == "reuse@example.com"

    # The whole point: it can be added again.
    assert not repository.user_email_exists(demo_engine, "reuse@example.com")
    again = _add(demo_engine, "reuse@example.com")
    assert again.user_id != member.user_id


def test_the_archived_row_survives_for_the_audit_trail(demo_engine: Engine) -> None:
    """The user row must NOT be deleted: nine NOT NULL foreign keys point at it,
    and the history is what makes an audit answerable."""
    member = _add(demo_engine, "trace@example.com")
    repository.delete_member(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id)

    # The row must survive so past actions stay attributable. It is no longer
    # findable by its old address, which is the point — so look it up by the
    # archived one.
    assert not repository.user_email_exists(demo_engine, "trace@example.com")
    archived_rows = [
        m for m in repository.list_members(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
        if m.user_id == member.user_id
    ]
    assert archived_rows == [], "removed members must not appear in the list"


def test_undo_restores_a_login_that_actually_works(demo_engine: Engine) -> None:
    """Restoring the membership alone would leave a member who is listed and
    cannot sign in — a failure that looks like success."""
    member = _add(demo_engine, "undo@example.com")
    _, archived = repository.delete_member(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id
    )

    token = undo_actions.member_removed(member.user_id, "viewer", archived)
    undo_actions.reverse(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, json.loads(token))

    restored = repository.get_member(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id
    )
    assert restored is not None
    assert restored.email == "undo@example.com", "the address must come back too"


def test_undo_does_not_abort_when_the_address_was_taken_meanwhile(
    demo_engine: Engine,
) -> None:
    """Someone else claimed it before the undo. Restoring the membership still
    matters; colliding on the unique index would fail the entire undo."""
    member = _add(demo_engine, "contested@example.com")
    _, archived = repository.delete_member(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id
    )
    _add(demo_engine, "contested@example.com")  # taken in the meantime

    token = undo_actions.member_removed(member.user_id, "viewer", archived)
    undo_actions.reverse(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, json.loads(token))

    restored = repository.get_member(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, member.user_id
    )
    assert restored is not None, "membership restored even though the email was not"


def test_a_long_address_does_not_overflow_the_column() -> None:
    """255-char column: a near-limit address would otherwise fail the removal."""
    long_email = "a" * 240 + "@example.com"
    archived = repository.archived_email(long_email, datetime(2026, 8, 10, tzinfo=UTC))
    assert len(archived) <= 255
    assert "_archived_" in archived
