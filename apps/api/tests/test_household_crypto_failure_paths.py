"""Every way ``household_crypto`` can fail, exercised on purpose.

A crypto module's error paths are the ones nobody runs by accident and the ones
that hurt when they are wrong — the August incident lived entirely in code that
"obviously" worked. These tests exist to drive the module to full coverage, so a
refusal, a fallback, or a swallowed exception cannot be edited into something
else without a test noticing.
"""

import base64

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy import text as sql_text

from family_cfo_api import household_crypto, models, repository
from family_cfo_api.config import get_settings


@pytest.fixture
def _master_key(monkeypatch):
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


@pytest.fixture
def _no_master_key(monkeypatch):
    monkeypatch.delenv("FAMILY_CFO_MASTER_KEY", raising=False)
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


def _make_sealable(engine, household_id: str) -> None:
    member = repository.list_members(engine, household_id)[0]
    household_crypto.ensure_member_wrap(engine, household_id, member.user_id, "demo-password")
    household_crypto.generate_recovery_key(engine, household_id)


# --- master key problems ------------------------------------------------------


def test_a_malformed_master_key_disables_encryption(demo_engine, monkeypatch, caplog) -> None:
    """Better off plainly than half-on: a key that is not a Fernet key must not
    leave the box writing ciphertext it cannot read back."""
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", "not-a-fernet-key")
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    hh = repository.list_households(demo_engine)[0]

    with caplog.at_level("ERROR"):
        assert household_crypto.enabled() is False
        assert household_crypto.encrypt_text(demo_engine, hh, "plain") == "plain"
    assert "not a valid Fernet key" in caplog.text
    get_settings.cache_clear()


def test_encrypted_rows_with_the_key_removed_say_so(_master_key, demo_engine, monkeypatch) -> None:
    hh = repository.list_households(demo_engine)[0]
    stored = household_crypto.encrypt_text(demo_engine, hh, "a sealed sentence")

    monkeypatch.delenv("FAMILY_CFO_MASTER_KEY", raising=False)
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    assert (
        household_crypto.decrypt_text(demo_engine, hh, stored)
        == "[encrypted — master key unavailable]"
    )


# --- key row creation races ---------------------------------------------------


def test_losing_the_key_creation_race_adopts_the_winner(
    _master_key, demo_engine, monkeypatch
) -> None:
    """Two processes reach a household with no key row at the same moment. The
    loser's INSERT hits the unique index; it must adopt the row that won rather
    than raise, and must never mint a second key for the same household."""
    hh = repository.list_households(demo_engine)[0]
    winner = household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet())
    household_crypto.reset_cache_for_tests()

    real = household_crypto._wrapped_dek_row
    seen: list[int] = []

    def blind_once(engine, household_id):
        """Blind on the first look — the window in which both processes believe
        no key row exists — then honest, as the retry inside the except is."""
        seen.append(1)
        return None if len(seen) == 1 else real(engine, household_id)

    monkeypatch.setattr(household_crypto, "_wrapped_dek_row", blind_once)
    adopted = household_crypto._get_or_create_dek(
        demo_engine, hh, household_crypto._master_fernet()
    )
    assert adopted == winner
    with demo_engine.connect() as conn:
        assert conn.execute(select(models.household_keys)).all().__len__() == 1


def test_a_key_insert_that_fails_for_any_other_reason_still_raises(
    _master_key, demo_engine, monkeypatch
) -> None:
    """Only a lost race is recoverable. If the row still isn't there on the
    retry, the failure was real and must not be swallowed."""
    hh = repository.list_households(demo_engine)[0]
    household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet())
    household_crypto.reset_cache_for_tests()

    monkeypatch.setattr(household_crypto, "_wrapped_dek_row", lambda *_a: None)
    with pytest.raises(Exception, match="UNIQUE constraint"):
        household_crypto._get_or_create_dek(
            demo_engine, hh, household_crypto._master_fernet()
        )


def test_caching_a_key_for_a_household_with_no_key_row_is_a_no_op(
    _master_key, demo_engine
) -> None:
    household_crypto._cache_put(demo_engine, "no-such-household", b"x" * 44)
    with household_crypto._cache_lock:
        assert "no-such-household" not in household_crypto._dek_cache


# --- canary edge cases --------------------------------------------------------


def test_canary_absent_falls_back_to_comparing_the_box_wrap(_master_key, demo_engine) -> None:
    """Pre-Phase-3 rows have no canary. A box wrap is then the only thing to
    check a posted key against — and it must actually be checked."""
    hh = repository.list_households(demo_engine)[0]
    dek = household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet())
    with demo_engine.begin() as conn:
        conn.execute(sql_text("update household_keys set canary = null"))

    assert household_crypto._canary_ok(demo_engine, hh, dek) is True
    assert household_crypto._canary_ok(demo_engine, hh, Fernet.generate_key()) is False


def test_canary_absent_and_no_key_row_accepts(_master_key, demo_engine) -> None:
    """Nothing recorded to check against: accept rather than lock a household
    out of a key it may legitimately hold."""
    assert household_crypto._canary_ok(demo_engine, "unknown-household", b"x" * 44) is True


def test_canary_absent_with_no_master_key_refuses(_master_key, demo_engine, monkeypatch) -> None:
    hh = repository.list_households(demo_engine)[0]
    dek = household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet())
    with demo_engine.begin() as conn:
        conn.execute(sql_text("update household_keys set canary = null"))
    monkeypatch.delenv("FAMILY_CFO_MASTER_KEY", raising=False)
    get_settings.cache_clear()
    assert household_crypto._canary_ok(demo_engine, hh, dek) is False


def test_a_corrupt_canary_refuses_every_key(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    dek = household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet())
    with demo_engine.begin() as conn:
        conn.execute(sql_text("update household_keys set canary = 'not-a-fernet-token'"))
    assert household_crypto._canary_ok(demo_engine, hh, dek) is False


# --- unlock paths -------------------------------------------------------------


def test_recovery_unlock_without_a_recovery_wrap_is_false(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    assert household_crypto.unlock_with_recovery_key(demo_engine, hh, "FCFO-anything") is False


def test_recovery_unlock_with_the_wrong_key_is_false(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    assert household_crypto.generate_recovery_key(demo_engine, hh) is not None
    assert household_crypto.unlock_with_recovery_key(demo_engine, hh, "FCFO-wrong") is False


def test_recovery_unlock_restores_a_sealed_household(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    household_crypto.ensure_member_wrap(demo_engine, hh, member.user_id, "demo-password")
    secret = household_crypto.generate_recovery_key(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None
    household_crypto.reset_cache_for_tests()

    assert household_crypto.dek_available(demo_engine, hh) is False
    assert household_crypto.unlock_with_recovery_key(demo_engine, hh, secret) is True
    assert household_crypto.dek_available(demo_engine, hh) is True


def test_a_stale_box_wrap_is_reopened_by_the_keyring(_master_key, demo_engine) -> None:
    """Restored onto new hardware: the wrap exists but this master key cannot
    open it. A live session key is the way back in."""
    hh = repository.list_households(demo_engine)[0]
    dek = household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet())
    with demo_engine.begin() as conn:
        conn.execute(
            sql_text("update household_keys set wrapped_dek = :w"),
            {"w": Fernet(Fernet.generate_key()).encrypt(dek).decode()},
        )
    household_crypto.reset_cache_for_tests()

    with pytest.raises(household_crypto.HouseholdLockedError):
        household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet())

    assert household_crypto.unlock_household(demo_engine, hh, dek) is True
    assert (
        household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet()) == dek
    )


def test_a_stale_box_wrap_serves_the_keyring_key_before_any_healing(
    _master_key, demo_engine
) -> None:
    """The window inside unlock_household between opening the keyring and
    re-minting the wrap — and the state a sealed household stays in, since
    healing deliberately skips it. Reads must work from the session key alone."""
    hh = repository.list_households(demo_engine)[0]
    dek = household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet())
    with demo_engine.begin() as conn:
        conn.execute(
            sql_text("update household_keys set wrapped_dek = :w"),
            {"w": Fernet(Fernet.generate_key()).encrypt(dek).decode()},
        )
    household_crypto.reset_cache_for_tests()

    household_crypto._keyring_put(demo_engine, hh, dek)
    assert (
        household_crypto._resolve_dek(demo_engine, hh, household_crypto._master_fernet()) == dek
    )


def test_healing_a_box_wrap_needs_a_master_key(_no_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    household_crypto._heal_box_wrap(demo_engine, hh, b"x" * 44)  # returns, writes nothing
    with demo_engine.connect() as conn:
        assert conn.execute(select(models.household_keys)).first() is None


# --- seal / unseal refusals ---------------------------------------------------


def test_seal_and_unseal_need_encryption_enabled(_no_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    assert "no master key" in household_crypto.seal_household(demo_engine, hh)
    assert "no master key" in household_crypto.unseal_household(demo_engine, hh)


def test_seal_needs_a_member_wrap_then_a_recovery_key(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    assert "member key" in household_crypto.seal_household(demo_engine, hh)

    member = repository.list_members(demo_engine, hh)[0]
    household_crypto.ensure_member_wrap(demo_engine, hh, member.user_id, "demo-password")
    assert "recovery key" in household_crypto.seal_household(demo_engine, hh)


def test_unseal_needs_the_household_unlocked(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None
    household_crypto.reset_cache_for_tests()  # the session that sealed it is gone
    assert "Unlock the household first" in household_crypto.unseal_household(demo_engine, hh)


# --- wrap upkeep never breaks a login ----------------------------------------


def test_member_wrap_upkeep_swallows_failures(_master_key, demo_engine, monkeypatch, caplog) -> None:
    """Key upkeep is best-effort by design: a login must not fail over it."""
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]

    def boom(*_args, **_kwargs):
        raise RuntimeError("wrap store unavailable")

    monkeypatch.setattr(household_crypto, "_upsert_wrap", boom)
    with caplog.at_level("ERROR"):
        household_crypto.ensure_member_wrap(demo_engine, hh, member.user_id, "pw")
    assert "member wrap upkeep failed" in caplog.text


def test_device_wrap_upkeep_swallows_failures(_master_key, demo_engine, caplog) -> None:
    hh = repository.list_households(demo_engine)[0]
    with caplog.at_level("ERROR"):
        household_crypto.ensure_device_wrap(demo_engine, hh, "device-1", "not base64 at all!!")
    assert "device wrap upkeep failed" in caplog.text


def test_device_wrap_is_skipped_when_encryption_is_off(_no_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    household_crypto.ensure_device_wrap(demo_engine, hh, "device-1", base64.b64encode(b"x" * 65).decode())
    assert household_crypto.device_wrap_json(demo_engine, hh, "device-1") is None


def test_device_wrap_json_round_trips(_master_key, demo_engine) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    hh = repository.list_households(demo_engine)[0]
    raw = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    assert household_crypto.device_wrap_json(demo_engine, hh, "device-1") is None
    household_crypto.ensure_device_wrap(
        demo_engine, hh, "device-1", base64.b64encode(raw).decode()
    )
    assert household_crypto.device_wrap_json(demo_engine, hh, "device-1") is not None


def test_member_wrap_refuses_a_key_that_fails_the_canary(
    _master_key, demo_engine, monkeypatch, caplog
) -> None:
    """A sealed, locked household opened by a wrong key would poison every
    subsequent write — which is precisely what the canary is for."""
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    household_crypto.ensure_member_wrap(demo_engine, hh, member.user_id, "demo-password")
    household_crypto.generate_recovery_key(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None
    household_crypto.reset_cache_for_tests()

    # The member's wrap opens, but the key inside no longer matches the canary.
    with demo_engine.begin() as conn:
        conn.execute(sql_text("update household_keys set canary = :c"), {"c": _foreign_canary()})

    with caplog.at_level("ERROR"):
        household_crypto.ensure_member_wrap(demo_engine, hh, member.user_id, "demo-password")
    assert "failed canary check" in caplog.text
    assert household_crypto.dek_available(demo_engine, hh) is False


def _foreign_canary() -> str:
    other = Fernet.generate_key()
    return household_crypto._subkey_fernet(other, b"rows").encrypt(
        household_crypto.CANARY_PLAINTEXT
    ).decode()


def test_recovery_key_needs_encryption_enabled(_no_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    assert household_crypto.generate_recovery_key(demo_engine, hh) is None


# --- password change refusals -------------------------------------------------


def test_password_change_is_refused_when_the_wrap_stays_stale(
    _master_key, demo_engine, monkeypatch, caplog
) -> None:
    """The whole point of on_password_changed: a member whose hash moved but
    whose wrap did not can log in and open nothing."""
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    household_crypto.ensure_member_wrap(demo_engine, hh, member.user_id, "old-password")

    monkeypatch.setattr(household_crypto, "ensure_member_wrap", lambda *a, **k: None)
    with caplog.at_level("ERROR"):
        changed = household_crypto.on_password_changed(
            demo_engine, hh, member.user_id, "old-password", "new-password"
        )
    assert changed is False
    assert "does not open with the new password" in caplog.text


def test_password_change_is_refused_when_no_wrap_exists(
    _master_key, demo_engine, monkeypatch, caplog
) -> None:
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    monkeypatch.setattr(household_crypto, "ensure_member_wrap", lambda *a, **k: None)
    with caplog.at_level("ERROR"):
        changed = household_crypto.on_password_changed(
            demo_engine, hh, member.user_id, "old", "new"
        )
    assert changed is False
    assert "left no member wrap" in caplog.text


def test_password_change_is_refused_when_the_new_wrap_fails_the_canary(
    _master_key, demo_engine, caplog
) -> None:
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    household_crypto.ensure_member_wrap(demo_engine, hh, member.user_id, "old-password")
    with demo_engine.begin() as conn:
        conn.execute(sql_text("update household_keys set canary = :c"), {"c": _foreign_canary()})

    with caplog.at_level("ERROR"):
        changed = household_crypto.on_password_changed(
            demo_engine, hh, member.user_id, "old-password", "new-password"
        )
    assert changed is False
    assert "failed the canary" in caplog.text


def test_password_change_is_a_no_op_without_encryption(_no_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    assert (
        household_crypto.on_password_changed(demo_engine, hh, member.user_id, "old", "new") is True
    )


# --- rotation row ownership ---------------------------------------------------


def test_rotation_moves_conversation_and_document_rows_and_spares_other_households(
    _master_key, demo_engine
) -> None:
    """The two indirect resolvers (a message via its conversation, extracted
    text via its document) and the ownership check that keeps a rotation inside
    one household."""
    hh = repository.list_households(demo_engine)[0]
    user_id = repository.list_members(demo_engine, hh)[0].user_id
    conversation = repository.create_conversation(demo_engine, hh, user_id, "Rotation")
    rec_id = repository.create_recommendation(
        demo_engine, hh, None, "answer", [], [], [], [], 0.9, [], [], "llm"
    )
    repository.append_conversation_turn(
        demo_engine, conversation.id, "what do we spend on food", "about the usual", rec_id
    )
    document = repository.create_document(demo_engine, hh, "image/png", "/tmp/receipt.png")
    repository.create_document_extraction(
        demo_engine, document.id, "ocr", "TOTAL 12.34", {}, 0.9, []
    )

    other = repository.create_household_with_owner(
        demo_engine, "Other family", "USD", "other@example.test", "hash", "Other"
    ).household_id
    repository.upsert_household_memory(demo_engine, other, "pet", "a tortoise")

    assert household_crypto.rotate_household_key(demo_engine, hh) is True

    messages = repository.list_conversation_messages(demo_engine, conversation.id)
    assert "about the usual" in [m.content for m in messages]
    documents = repository.list_documents_with_extractions(demo_engine, hh)
    assert any(
        extraction is not None and extraction.text == "TOTAL 12.34"
        for _document, extraction in documents
    )
    # The other household's row was never touched and still reads.
    assert [m.value for m in repository.list_household_memories(demo_engine, other)] == [
        "a tortoise"
    ]
