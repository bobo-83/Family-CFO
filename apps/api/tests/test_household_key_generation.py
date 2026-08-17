"""Migration 0091: a key change must reach every process, and a rotation must
never create a key nothing durable can hold.

The incident these encode, in one sentence: the background worker cached a
household DEK, missed both a seal and a rotation because ``_resolve_dek``
trusted its own cache before it trusted the database, and went on writing rows
under a retired key for ~34 hours — 225 values that no surviving key could open.

Two independent failures had to line up, so both get their own tests:

* the cache never expired against anything, so a key change reached only the
  process that made it;
* rotation deleted the member and recovery wraps and re-minted device wraps for
  a household that had no live device, leaving the new key in one process's
  memory with every row already re-encrypted under it.
"""

import base64
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy import text as sql_text

from family_cfo_api import household_crypto, models, repository
from family_cfo_api.config import get_settings

REVALIDATE = household_crypto.DEK_CACHE_REVALIDATE_SECONDS


@pytest.fixture
def _master_key(monkeypatch):
    # Generated per test run — nothing key-shaped is ever committed.
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


@pytest.fixture
def clock(monkeypatch):
    """A hand-cranked monotonic clock, so cache windows are tested without
    sleeping through them."""
    now = {"t": 1_000.0}
    monkeypatch.setattr(household_crypto, "_now", lambda: now["t"])

    def advance(seconds: float) -> None:
        now["t"] += seconds

    return advance


def _generation(engine, household_id: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            select(models.household_keys.c.key_generation).where(
                models.household_keys.c.household_id == household_id
            )
        ).scalar_one()


def _cached(household_id: str):
    with household_crypto._cache_lock:
        return household_crypto._dek_cache.get(household_id)


def _become_another_process(household_id: str, entry) -> None:
    """Model a SECOND process: it holds the DEK and generation it cached before
    the change, and has no session keyring of its own."""
    household_crypto.reset_cache_for_tests()
    with household_crypto._cache_lock:
        household_crypto._dek_cache[household_id] = entry


def _p256_public_key_b64() -> str:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    return base64.b64encode(raw).decode()


def _pair_a_device(engine, household_id: str, user_id: str, name: str = "test phone") -> str:
    repository.create_paired_device_with_session(
        engine,
        household_id=household_id,
        user_id=user_id,
        device_name=name,
        device_public_key=_p256_public_key_b64(),
        access_token=f"token-{name}",
        token_hash=f"hash-{name}",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    device = next(
        d for d in repository.list_paired_devices(engine, household_id) if d.name == name
    )
    return device.id


def _make_sealable(engine, household_id: str) -> None:
    """Seal's own preconditions: one member wrap and a recovery key."""
    member = repository.list_members(engine, household_id)[0]
    household_crypto.ensure_member_wrap(engine, household_id, member.user_id, "demo-password")
    household_crypto.generate_recovery_key(engine, household_id)


# --- the cache must keep proving it is current -------------------------------


def test_a_stale_cache_cannot_write_under_a_retired_key(
    _master_key, demo_engine, clock
) -> None:
    """THE regression test. A process holding a pre-seal DEK must be locked out
    rather than allowed to encrypt with a key the household has retired."""
    hh = repository.list_households(demo_engine)[0]
    household_crypto.encrypt_text(demo_engine, hh, "warms the cache")
    stale = _cached(hh)
    assert stale is not None

    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None

    _become_another_process(hh, stale)

    # Inside the revalidation window the cache is still trusted — that is the
    # deliberate cost of not reading the database on every single value.
    assert household_crypto.encrypt_text(demo_engine, hh, "still allowed").startswith(
        household_crypto.ENC_PREFIX
    )

    # Past it, the generation no longer matches and the write is refused. Before
    # 0091 this line wrote an unreadable row and told nobody.
    clock(REVALIDATE + 1)
    with pytest.raises(household_crypto.HouseholdLockedError):
        household_crypto.encrypt_text(demo_engine, hh, "would have been unreadable")


def test_a_stale_cache_picks_up_a_rotation(_master_key, demo_engine, clock) -> None:
    """Convenient mode: the box wrap holds the new key, so a stale process does
    not just fail — it re-reads and recovers."""
    hh = repository.list_households(demo_engine)[0]
    repository.upsert_household_memory(demo_engine, hh, "pet", "a golden retriever")
    stale = _cached(hh)
    assert stale is not None

    assert household_crypto.rotate_household_key(demo_engine, hh) is True
    _become_another_process(hh, stale)
    clock(REVALIDATE + 1)

    values = [m.value for m in repository.list_household_memories(demo_engine, hh)]
    assert "a golden retriever" in values
    assert _cached(hh).generation == _generation(demo_engine, hh)


def test_generation_is_reread_at_most_once_per_window(
    _master_key, demo_engine, clock, monkeypatch
) -> None:
    """The hot path stays in memory: thousands of values per page load must not
    become thousands of queries."""
    hh = repository.list_households(demo_engine)[0]
    household_crypto.encrypt_text(demo_engine, hh, "warms the cache")

    reads: list[str] = []
    real = household_crypto._current_generation

    def counting(engine, household_id):
        reads.append(household_id)
        return real(engine, household_id)

    monkeypatch.setattr(household_crypto, "_current_generation", counting)

    for _ in range(25):
        household_crypto.encrypt_text(demo_engine, hh, "hot path")
    assert reads == []

    clock(REVALIDATE + 1)
    household_crypto.encrypt_text(demo_engine, hh, "one revalidation")
    assert len(reads) == 1

    for _ in range(25):
        household_crypto.encrypt_text(demo_engine, hh, "hot again")
    assert len(reads) == 1


def test_session_keyring_revalidates_against_the_generation(
    _master_key, demo_engine, clock
) -> None:
    """The keyring needs the same discipline as the box-wrap cache: a sealed
    household's unlocked key is just as capable of going stale."""
    hh = repository.list_households(demo_engine)[0]
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None
    assert household_crypto.dek_available(demo_engine, hh) is True

    # Whatever another process did, this is all that is visible from here.
    with demo_engine.begin() as conn:
        conn.execute(sql_text("update household_keys set key_generation = key_generation + 1"))

    assert household_crypto.dek_available(demo_engine, hh) is True  # inside the window
    clock(REVALIDATE + 1)
    assert household_crypto.dek_available(demo_engine, hh) is False  # locked, not stale


def test_keyring_does_not_resurrect_a_key_dropped_mid_check(
    _master_key, demo_engine, clock, monkeypatch
) -> None:
    """The generation read hits the database with the cache lock released. If a
    seal or rotation drops the key in that window, the reader must not hand it
    back — putting it back is how a retired key survives."""
    hh = repository.list_households(demo_engine)[0]
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None
    clock(REVALIDATE + 1)  # force the revalidation path

    real = household_crypto._current_generation

    def another_thread_seals(engine, household_id):
        generation = real(engine, household_id)
        household_crypto._invalidate(household_id)
        return generation  # unchanged, so only the write-back guard can catch it

    monkeypatch.setattr(household_crypto, "_current_generation", another_thread_seals)

    assert household_crypto._keyring_get(demo_engine, hh) is None
    with household_crypto._cache_lock:
        assert hh not in household_crypto._session_keyring


def test_session_keyring_still_expires_on_its_own_ttl(_master_key, demo_engine, clock) -> None:
    """0091 must not have quietly extended sealed mode's real guarantee."""
    hh = repository.list_households(demo_engine)[0]
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None
    assert household_crypto.dek_available(demo_engine, hh) is True

    clock(household_crypto.SESSION_KEYRING_TTL_SECONDS + 1)
    assert household_crypto.dek_available(demo_engine, hh) is False


def test_activity_slides_the_keyring_ttl(_master_key, demo_engine, clock) -> None:
    hh = repository.list_households(demo_engine)[0]
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None

    for _ in range(4):
        clock(household_crypto.SESSION_KEYRING_TTL_SECONDS * 0.6)
        assert household_crypto.dek_available(demo_engine, hh) is True


def test_seal_unseal_and_rotate_each_bump_the_generation(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    household_crypto.encrypt_text(demo_engine, hh, "creates the key row")
    start = _generation(demo_engine, hh)

    assert household_crypto.rotate_household_key(demo_engine, hh) is True
    after_rotate = _generation(demo_engine, hh)
    assert after_rotate > start

    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None
    after_seal = _generation(demo_engine, hh)
    assert after_seal > after_rotate

    assert household_crypto.unseal_household(demo_engine, hh) is None
    assert _generation(demo_engine, hh) > after_seal


def test_healing_a_stale_box_wrap_bumps_the_generation(_master_key, demo_engine) -> None:
    """Re-minting the wrap under a new master key changes what other processes
    must unwrap, so it counts as a key change like any other."""
    hh = repository.list_households(demo_engine)[0]
    dek = household_crypto._resolve_dek(
        demo_engine, hh, household_crypto._master_fernet()
    )
    before = _generation(demo_engine, hh)

    # Simulate a wrap this box's master key cannot open (restored hardware).
    with demo_engine.begin() as conn:
        conn.execute(
            sql_text("update household_keys set wrapped_dek = :w"),
            {"w": Fernet(Fernet.generate_key()).encrypt(dek).decode()},
        )
    household_crypto._heal_box_wrap(demo_engine, hh, dek)
    assert _generation(demo_engine, hh) > before


# --- a rotation must never strand the key it creates --------------------------


def test_rotation_is_refused_when_sealed_with_no_device(_master_key, demo_engine) -> None:
    """Sealed, no device: rotation would delete the member and recovery wraps,
    re-mint nothing, and leave the new key in memory only."""
    hh = repository.list_households(demo_engine)[0]
    repository.upsert_household_memory(demo_engine, hh, "pet", "a golden retriever")
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None

    assert household_crypto.rotation_would_strand_key(demo_engine, hh) is True
    assert household_crypto.rotate_household_key(demo_engine, hh) is False

    # Refused means untouched: rows, wraps, and generation all as they were.
    values = [m.value for m in repository.list_household_memories(demo_engine, hh)]
    assert "a golden retriever" in values
    status = household_crypto.wrap_status(demo_engine, hh)
    assert status["member_wraps"] == 1
    assert status["has_recovery_key"] is True


def test_rotation_proceeds_when_sealed_with_a_live_device(_master_key, demo_engine) -> None:
    """A revoked device sits alongside a live one: the new key must go to the
    live device only. Wrapping it for a revoked device would hand the key back
    to something the household deliberately cut off."""
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    repository.upsert_household_memory(demo_engine, hh, "pet", "a golden retriever")
    _pair_a_device(demo_engine, hh, member.user_id, "live phone")
    gone = _pair_a_device(demo_engine, hh, member.user_id, "revoked phone")
    assert repository.revoke_paired_device(demo_engine, hh, gone) is True
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None

    assert household_crypto.rotation_would_strand_key(demo_engine, hh) is False
    assert household_crypto.rotate_household_key(demo_engine, hh) is True

    # The new key has a durable home — one, not two — and the rows moved with it.
    assert household_crypto.wrap_status(demo_engine, hh)["device_wraps"] == 1
    values = [m.value for m in repository.list_household_memories(demo_engine, hh)]
    assert "a golden retriever" in values


def test_rotation_aborts_when_no_device_wrap_can_actually_be_minted(
    _master_key, demo_engine, monkeypatch
) -> None:
    """#112: passing the precondition proves a wrap COULD be minted. Only
    minting proves one WAS. A device whose stored key cannot produce a wrap
    must abort the rotation while nothing has been re-encrypted yet."""
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    repository.upsert_household_memory(demo_engine, hh, "pet", "a golden retriever")
    _pair_a_device(demo_engine, hh, member.user_id)
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None

    # The precondition is satisfied — a live device with a stored public key.
    assert household_crypto.rotation_would_strand_key(demo_engine, hh) is False

    def unmintable(*_args, **_kwargs):
        raise RuntimeError("stored public key will not parse")

    monkeypatch.setattr(household_crypto, "_wrap_with_p256", unmintable)
    assert household_crypto.rotate_household_key(demo_engine, hh) is False

    # Aborted before any row moved: the household still reads with its old key.
    values = [m.value for m in repository.list_household_memories(demo_engine, hh)]
    assert "a golden retriever" in values
    assert household_crypto.wrap_status(demo_engine, hh)["member_wraps"] == 1


def test_rotation_survives_one_bad_device_when_another_holds_the_key(
    _master_key, demo_engine, monkeypatch
) -> None:
    """One unusable device must not veto a rotation that another device can
    carry — abort only when NOTHING durable holds the new key."""
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    repository.upsert_household_memory(demo_engine, hh, "pet", "a golden retriever")
    good = _pair_a_device(demo_engine, hh, member.user_id, "good phone")
    _pair_a_device(demo_engine, hh, member.user_id, "bad phone")
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None

    real = household_crypto._wrap_with_p256
    calls: list[int] = []

    def one_bad(dek, raw):
        calls.append(1)
        if len(calls) == 1:  # whichever device comes first fails
            raise RuntimeError("stored public key will not parse")
        return real(dek, raw)

    monkeypatch.setattr(household_crypto, "_wrap_with_p256", one_bad)
    assert household_crypto.rotate_household_key(demo_engine, hh) is True
    assert good  # the surviving device is what carried the key

    assert household_crypto.wrap_status(demo_engine, hh)["device_wraps"] == 1
    values = [m.value for m in repository.list_household_memories(demo_engine, hh)]
    assert "a golden retriever" in values


def test_rotation_proceeds_for_a_convenient_household_without_devices(
    _master_key, demo_engine
) -> None:
    """Not sealed: the box wrap is itself a durable holder, so nothing is at
    risk and the precondition must not fire."""
    hh = repository.list_households(demo_engine)[0]
    household_crypto.encrypt_text(demo_engine, hh, "creates the key row")
    assert household_crypto.rotation_would_strand_key(demo_engine, hh) is False
    assert household_crypto.rotate_household_key(demo_engine, hh) is True


def test_durable_wrap_holders_ignores_revoked_and_keyless_devices(
    _master_key, demo_engine
) -> None:
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    assert household_crypto.durable_wrap_holders(demo_engine, hh) == 0

    keeper = _pair_a_device(demo_engine, hh, member.user_id, "keeper")
    revoked = _pair_a_device(demo_engine, hh, member.user_id, "revoked")
    assert household_crypto.durable_wrap_holders(demo_engine, hh) == 2

    assert repository.revoke_paired_device(demo_engine, hh, revoked) is True
    assert household_crypto.durable_wrap_holders(demo_engine, hh) == 1
    assert keeper  # the surviving device is the one still counted


def test_durable_wrap_holders_ignores_a_device_with_no_public_key(
    _master_key, demo_engine, monkeypatch
) -> None:
    """``paired_devices.public_key`` is NOT NULL in the schema, but the record
    type allows None and a wrap cannot be minted without it. Guarding on the
    record rather than trusting the column keeps the two from disagreeing."""
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]
    _pair_a_device(demo_engine, hh, member.user_id, "keyless")

    real = repository.list_paired_devices

    def keyless(engine, household_id):
        return [replace(d, public_key=None) for d in real(engine, household_id)]

    monkeypatch.setattr(repository, "list_paired_devices", keyless)
    assert household_crypto.durable_wrap_holders(demo_engine, hh) == 0


def test_stranding_check_is_off_when_encryption_is_off(demo_engine, monkeypatch) -> None:
    monkeypatch.delenv("FAMILY_CFO_MASTER_KEY", raising=False)
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    hh = repository.list_households(demo_engine)[0]
    assert household_crypto.rotation_would_strand_key(demo_engine, hh) is False
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_removing_a_member_is_refused_rather_than_stranding_the_key(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """Through the route: the member must still be there afterwards. Removing
    them and then failing to rotate is the worst of both outcomes."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    hh = repository.list_households(demo_engine)[0]
    doomed = repository.create_member(
        demo_engine,
        household_id=hh,
        email="second@example.test",
        password_hash="not-a-real-hash",
        display_name="Second",
        role="adult",
    )
    _make_sealable(demo_engine, hh)
    assert household_crypto.seal_household(demo_engine, hh) is None

    refused = await demo_client.delete(
        f"/api/v1/household/members/{doomed.user_id}", headers=headers
    )
    assert refused.status_code == 409, refused.text
    assert "sealed" in refused.json()["error"]["message"]

    still_there = [m.user_id for m in repository.list_members(demo_engine, hh)]
    assert doomed.user_id in still_there
