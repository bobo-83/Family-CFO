"""#97: a member changes their own password.

The interesting assertions are not "the new password logs in" — they are about
the KEY. The password derives the member's household key wrap (ADR 0072), so a
change that moves ``users.password_hash`` without re-minting the wrap leaves a
member who authenticates fine and cannot decrypt a thing, or — worse — leaves
the RETIRED password still opening the key. Both are tested here, and the
sealed-household case is the one that fails if the crypto seam is skipped.
"""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from family_cfo_api import fixtures, household_crypto, models, repository, security
from family_cfo_api.config import get_settings

OLD_PASSWORD = fixtures.DEMO_USER_PASSWORD
NEW_PASSWORD = "a-different-passphrase-97"


@pytest.fixture
def _master_key(monkeypatch):
    # Generated per test run — nothing key-shaped is ever committed.
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


async def _change(client, token, current=OLD_PASSWORD, new=NEW_PASSWORD):
    return await client.post(
        "/api/v1/auth/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": current, "new_password": new},
    )


def _member_wrap(engine, household_id, user_id):
    with engine.connect() as conn:
        return conn.execute(
            select(models.household_key_wraps.c.wrap_json).where(
                models.household_key_wraps.c.household_id == household_id,
                models.household_key_wraps.c.kind == "member",
                models.household_key_wraps.c.subject_id == user_id,
            )
        ).all()


@pytest.mark.anyio
async def test_old_password_rejected_and_new_one_accepted(demo_client, demo_token) -> None:
    assert (await _change(demo_client, demo_token)).status_code == 204

    stale = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": OLD_PASSWORD},
    )
    assert stale.status_code == 401

    fresh = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": NEW_PASSWORD},
    )
    assert fresh.status_code == 201
    assert fresh.json()["access_token"]


@pytest.mark.anyio
async def test_other_sessions_are_revoked_and_the_current_one_survives(
    demo_client, demo_token
) -> None:
    """Changing a password usually means somebody else has it — so every other
    session goes, and the one in hand does not (nobody wants to be signed out of
    the screen they just used)."""
    from tests.conftest import login

    other = await login(demo_client, fixtures.DEMO_USER_EMAIL, OLD_PASSWORD)
    assert other != demo_token

    assert (await _change(demo_client, demo_token)).status_code == 204

    dead = await demo_client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {other}"}
    )
    assert dead.status_code == 401
    alive = await demo_client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert alive.status_code == 200


@pytest.mark.anyio
async def test_another_members_sessions_are_left_alone(
    demo_client, demo_token, demo_viewer_token
) -> None:
    """"Other sessions" means this user's — not the rest of the household's."""
    assert (await _change(demo_client, demo_token)).status_code == 204
    still_in = await demo_client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {demo_viewer_token}"}
    )
    assert still_in.status_code == 200


@pytest.mark.anyio
async def test_wrong_current_password_is_refused_and_changes_nothing(
    demo_client, demo_token
) -> None:
    response = await _change(demo_client, demo_token, current="not-my-password")
    # 403, not 401: the session is valid — a 401 would trip the clients' dead
    # token handling and sign the member out over a typo.
    assert response.status_code == 403
    assert response.json()["error"]["code"]

    # The old password still works, i.e. nothing moved.
    replay = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": OLD_PASSWORD},
    )
    assert replay.status_code == 201


@pytest.mark.anyio
async def test_wrong_current_password_is_rate_limited(demo_client, demo_token) -> None:
    """Otherwise the change-password form is an unthrottled password oracle for
    anyone holding a session (ADR 0010)."""
    for _ in range(5):
        assert (
            await _change(demo_client, demo_token, current="not-my-password")
        ).status_code == 403

    locked = await _change(demo_client, demo_token, current="not-my-password")
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0

    # Locked out even WITH the right password — the throttle is on the check.
    assert (await _change(demo_client, demo_token)).status_code == 429


@pytest.mark.anyio
async def test_password_change_lockout_does_not_lock_the_member_out_of_login(
    demo_client, demo_token
) -> None:
    """Namespaced limiter keys: a hijacked session hammering this form must not
    be able to lock the real member out of signing in."""
    for _ in range(6):
        await _change(demo_client, demo_token, current="not-my-password")

    still_works = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": OLD_PASSWORD},
    )
    assert still_works.status_code == 201


@pytest.mark.anyio
async def test_reusing_the_current_password_is_refused(demo_client, demo_token) -> None:
    response = await _change(demo_client, demo_token, new=OLD_PASSWORD)
    assert response.status_code == 400


@pytest.mark.anyio
async def test_short_new_password_is_refused(demo_client, demo_token) -> None:
    """Same bar as the invite flow (min_length=8) — one standard, not two."""
    response = await _change(demo_client, demo_token, new="short")
    assert response.status_code == 422


@pytest.mark.anyio
async def test_change_password_requires_a_session(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/auth/password",
        json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_it_is_audited_as_irreversible(demo_client, demo_token, demo_engine) -> None:
    from family_cfo_api import undo_actions

    assert undo_actions.UNDO_POLICY["auth.password_changed"] == undo_actions.IRREVERSIBLE

    assert (await _change(demo_client, demo_token)).status_code == 204

    events = repository.list_audit_events(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, limit=50)
    entry = next(e for e in events if e.action == "auth.password_changed")
    assert entry.actor_user_id == fixtures.DEMO_USER_ID
    # An undo token would carry the old hash — a stored credential — and would
    # re-mint a wrap for the password being retired. There must not be one.
    assert entry.undo_token is None
    # And the summary must never quote either password.
    assert OLD_PASSWORD not in entry.summary
    assert NEW_PASSWORD not in entry.summary


# --- the key, not just the hash (ADR 0072) -----------------------------------


@pytest.mark.anyio
async def test_the_old_password_no_longer_unwraps_the_key(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """A DIFFERENT assertion from "the old password no longer logs in": a wrap
    the retired password still opens is a retired password that still works as a
    key, which is the whole thing #97 is trying to stop."""
    hh = fixtures.DEMO_HOUSEHOLD_ID
    user_id = fixtures.DEMO_USER_ID
    household_crypto.on_password_established(demo_engine, hh, user_id, OLD_PASSWORD)
    before = _member_wrap(demo_engine, hh, user_id)
    assert household_crypto.unwrap_with_password(before[0][0], OLD_PASSWORD) is not None

    assert (await _change(demo_client, demo_token)).status_code == 204

    after = _member_wrap(demo_engine, hh, user_id)
    # Replaced, not added to: a second surviving row would be a second key.
    assert len(after) == 1
    assert household_crypto.unwrap_with_password(after[0][0], OLD_PASSWORD) is None
    assert household_crypto.unwrap_with_password(after[0][0], NEW_PASSWORD) is not None


@pytest.mark.anyio
async def test_sealed_household_member_can_still_unlock_after_changing(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """THE test. In a sealed household the member wrap is the only way in, so a
    change that skips the crypto seam locks the member out of their own data —
    silently, because they can still sign in.

    The household is deliberately LOCKED at the moment of the change. That is
    not a contrived state: the session keyring's TTL is 30 minutes while a login
    session lasts hours, so a member who signs in and changes their password
    later that afternoon arrives here with no key in memory. It is also the only
    arrangement with teeth — sealing leaves the sealing session's key open, and
    a change made while unlocked passes even if the current password is never
    proven first.
    """
    hh = fixtures.DEMO_HOUSEHOLD_ID
    user_id = fixtures.DEMO_USER_ID
    headers = {"Authorization": f"Bearer {demo_token}"}
    repository.upsert_household_memory(demo_engine, hh, "sealed_fact", "known only inside")

    # Seal: needs a member wrap and a recovery key.
    household_crypto.on_password_established(demo_engine, hh, user_id, OLD_PASSWORD)
    await demo_client.post("/api/v1/household/recovery-key", headers=headers)
    sealed = await demo_client.post(
        "/api/v1/household/seal-mode", headers=headers, json={"mode": "sealed"}
    )
    assert sealed.status_code == 200, sealed.text
    assert sealed.json()["mode"] == "sealed"

    # Keyring expired (or the box restarted): sealed AND locked. The member's
    # own wrap — which opens with the password they are about to retire — is now
    # the only key in existence.
    household_crypto.reset_cache_for_tests()
    assert (await demo_client.get("/api/v1/memories", headers=headers)).status_code == 423

    assert (await _change(demo_client, demo_token)).status_code == 204

    # Box restart: the in-memory keyring is gone, so the wrap is the ONLY key.
    household_crypto.reset_cache_for_tests()
    locked = await demo_client.get("/api/v1/memories", headers=headers)
    assert locked.status_code == 423

    # The NEW password opens it — the wrap was re-minted, not left behind.
    household_crypto.on_password_established(demo_engine, hh, user_id, NEW_PASSWORD)
    memories = repository.list_household_memories(demo_engine, hh)
    assert any(m.value == "known only inside" for m in memories)

    # And the OLD one does not: it is not a key any more.
    household_crypto.reset_cache_for_tests()
    household_crypto.on_password_established(demo_engine, hh, user_id, OLD_PASSWORD)
    with pytest.raises(household_crypto.HouseholdLockedError):
        repository.list_household_memories(demo_engine, hh)


@pytest.mark.anyio
async def test_sealed_and_locked_change_is_refused_rather_than_half_applied(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """If the wrap cannot be re-minted, the hash must not move either. Half a
    change is worse than none: the member would sign in with a password that
    decrypts nothing."""
    hh = fixtures.DEMO_HOUSEHOLD_ID
    user_id = fixtures.DEMO_USER_ID
    headers = {"Authorization": f"Bearer {demo_token}"}

    # Seal, then delete the member wrap and drop the keyring: sealed, locked,
    # and no way for this member's password to reach the key.
    household_crypto.on_password_established(demo_engine, hh, user_id, OLD_PASSWORD)
    await demo_client.post("/api/v1/household/recovery-key", headers=headers)
    assert (
        await demo_client.post(
            "/api/v1/household/seal-mode", headers=headers, json={"mode": "sealed"}
        )
    ).status_code == 200

    from sqlalchemy import delete as sql_delete

    with demo_engine.begin() as conn:
        conn.execute(
            sql_delete(models.household_key_wraps).where(
                models.household_key_wraps.c.household_id == hh,
                models.household_key_wraps.c.kind == "member",
            )
        )
    household_crypto.reset_cache_for_tests()

    refused = await _change(demo_client, demo_token)
    assert refused.status_code == 409

    # Nothing moved: the old password is still the password.
    user = repository.get_user_by_id(demo_engine, user_id)
    assert security.verify_password(OLD_PASSWORD, user.password_hash)
    assert not security.verify_password(NEW_PASSWORD, user.password_hash)


@pytest.mark.anyio
async def test_convenient_household_reads_survive_the_change(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """The everyday case: sealed content stays readable, and the wrap is a
    working key afterwards rather than a decoration."""
    hh = fixtures.DEMO_HOUSEHOLD_ID
    repository.upsert_household_memory(demo_engine, hh, "fact", "a household detail")

    assert (await _change(demo_client, demo_token)).status_code == 204

    memories = repository.list_household_memories(demo_engine, hh)
    assert any(m.value == "a household detail" for m in memories)

    wrap = _member_wrap(demo_engine, hh, fixtures.DEMO_USER_ID)[0][0]
    dek = household_crypto.unwrap_with_password(wrap, NEW_PASSWORD)
    assert dek is not None
    assert household_crypto.unlock_household(demo_engine, hh, dek) is True


def test_change_password_is_the_only_writer_of_an_existing_hash() -> None:
    """#97 / ADR 0072: `users.password_hash` may not be written behind the
    crypto seam's back. Every other writer is an INSERT of a brand-new user
    (invite accept, member create, hosted-owner bootstrap), which has no wrap to
    invalidate; the one UPDATE lives in `set_user_password_hash`."""
    import pathlib
    import re

    source_root = pathlib.Path(repository.__file__).parent
    offenders = []
    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^.*password_hash\s*=.*$", text, re.MULTILINE):
            line = match.group(0)
            if "def " in line or "password_hash: str" in line:
                continue
            offenders.append((path.name, line.strip()))

    # An UPDATE of the users table setting password_hash: exactly one, and it is
    # the documented one.
    updates = [
        (name, line)
        for name, line in offenders
        if name == "repository.py" and "values(password_hash=password_hash)" in line
    ]
    assert len(updates) == 1, updates
