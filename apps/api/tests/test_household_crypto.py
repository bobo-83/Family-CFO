"""ADR 0072 Phase 1: per-household envelope encryption of content columns.

The DEK is wrapped by the box master key; chats, advisor answers, memories,
feedback notes, and document text are stored as enc1: tokens. Reads return
plaintext transparently; the raw database file must NOT contain the words.
"""

from sqlalchemy import select, text as sql_text

import pytest
from family_cfo_api import household_crypto, models, repository
from cryptography.fernet import Fernet

from family_cfo_api.config import get_settings


@pytest.fixture
def _master_key(monkeypatch):
    # Generated per test run — nothing key-shaped is ever committed.
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


def test_passthrough_when_disabled(demo_engine, monkeypatch) -> None:
    monkeypatch.delenv("FAMILY_CFO_MASTER_KEY", raising=False)
    household_crypto.reset_cache_for_tests()
    get_settings.cache_clear()
    hh = repository.list_households(demo_engine)[0]
    assert household_crypto.encrypt_text(demo_engine, hh, "plain words") == "plain words"
    assert household_crypto.decrypt_text(demo_engine, hh, "plain words") == "plain words"


def test_round_trip_and_ciphertext_at_rest(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    stored = household_crypto.encrypt_text(demo_engine, hh, "we eat out five times a week")
    assert stored.startswith(household_crypto.ENC_PREFIX)
    assert "eat out" not in stored
    assert (
        household_crypto.decrypt_text(demo_engine, hh, stored)
        == "we eat out five times a week"
    )
    # A DEK row was created, wrapped (not raw) in the database.
    with demo_engine.connect() as conn:
        wrapped = conn.execute(select(models.household_keys.c.wrapped_dek)).scalar_one()
    assert household_crypto.ENC_PREFIX not in wrapped  # it's a Fernet token, not enc1:
    assert len(wrapped) > 60


def test_cross_household_keys_do_not_decrypt(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    stored = household_crypto.encrypt_text(demo_engine, hh, "private to household A")
    other = household_crypto.decrypt_text(demo_engine, "other-household-id", stored)
    assert other == "[encrypted — key mismatch]"


def test_legacy_plaintext_reads_through(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    assert (
        household_crypto.decrypt_text(demo_engine, hh, "pre-encryption row")
        == "pre-encryption row"
    )


def test_memory_and_recommendation_rows_are_sealed(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]

    repository.upsert_household_memory(
        demo_engine, hh, "eating_out_frequency", "five times a week", source="chat"
    )
    memories = repository.list_household_memories(demo_engine, hh)
    assert any(m.value == "five times a week" for m in memories)

    rec_id = repository.create_recommendation(
        demo_engine,
        hh,
        None,
        "Skip the beach house this year.",
        [],
        [],
        [],
        [],
        0.9,
        [],
        [],
        "llm",
    )
    assert rec_id

    # The raw stored values are ciphertext — a dump of these tables leaks nothing.
    with demo_engine.connect() as conn:
        raw_memory = conn.execute(
            sql_text("select value from household_memories where key = 'eating_out_frequency'")
        ).scalar_one()
        raw_answer = conn.execute(
            sql_text("select answer from recommendations where id = :i"), {"i": rec_id}
        ).scalar_one()
    assert raw_memory.startswith(household_crypto.ENC_PREFIX)
    assert "five times" not in raw_memory
    assert raw_answer.startswith(household_crypto.ENC_PREFIX)
    assert "beach house" not in raw_answer


def test_conversation_turns_are_sealed_and_read_back(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    user_id = repository.list_members(demo_engine, hh)[0].user_id
    conversation = repository.create_conversation(demo_engine, hh, user_id, "Money chat")
    rec_id = repository.create_recommendation(
        demo_engine, hh, None, "answer", [], [], [], [], 0.9, [], [], "llm"
    )
    repository.append_conversation_turn(
        demo_engine, conversation.id, "can we afford a puppy", "yes, comfortably", rec_id
    )

    messages = repository.list_conversation_messages(demo_engine, conversation.id)
    assert [m.content for m in messages] == ["can we afford a puppy", "yes, comfortably"]

    with demo_engine.connect() as conn:
        raw = [
            row[0]
            for row in conn.execute(
                sql_text(
                    "select content from conversation_messages where conversation_id = :c"
                ),
                {"c": conversation.id},
            )
        ]
    assert all(value.startswith(household_crypto.ENC_PREFIX) for value in raw)
    assert all("puppy" not in value for value in raw)


def test_encrypt_existing_command_seals_legacy_rows(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    # A legacy plaintext row, as if written before the feature shipped.
    with demo_engine.begin() as conn:
        conn.execute(
            sql_text(
                "insert into household_memories"
                " (id, household_id, key, value, source, created_at, updated_at)"
                " values ('legacy-1', :h, 'legacy_key', 'legacy plain value', 'chat',"
                " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"h": hh},
        )

    from family_cfo_api.tools import encrypt_existing

    changed = encrypt_existing._encrypt_table(
        demo_engine,
        models.household_memories,
        models.household_memories.c.id,
        ["value"],
        lambda row: row["household_id"],
    )
    assert changed >= 1

    with demo_engine.connect() as conn:
        raw = conn.execute(
            sql_text("select value from household_memories where id = 'legacy-1'")
        ).scalar_one()
    assert raw.startswith(household_crypto.ENC_PREFIX)
    memories = repository.list_household_memories(demo_engine, hh)
    assert any(m.value == "legacy plain value" for m in memories)


def test_transactions_accounts_and_names_are_sealed(_master_key, demo_engine) -> None:
    """Batch 2 (the aggregation refactor): merchants, descriptions, notes,
    account/bill/income/goal names, audit summaries and undo tokens are
    ciphertext at rest while every reader returns plaintext."""
    hh = repository.list_households(demo_engine)[0]
    account = repository.create_account(demo_engine, hh, "Family checking", "checking", "USD")
    txn_id = repository.create_transaction(
        demo_engine,
        hh,
        account.id,
        __import__("datetime").date.today(),
        -4200,
        "USD",
        "Corner Coffee Shop",
        "flat white and a scone",
        None,
        None,
        "reviewed",
    )
    repository.set_transaction_note(demo_engine, hh, txn_id, "with grandma")
    bill = repository.create_bill(demo_engine, hh, "Metro Power", 20000, "USD", "monthly")

    # Readers return plaintext.
    assert account.name == "Family checking"
    txn = repository.get_transaction(demo_engine, hh, txn_id)
    assert txn.merchant == "Corner Coffee Shop"
    assert txn.description == "flat white and a scone"
    assert txn.note == "with grandma"
    assert any(b.name == "Metro Power" for b in repository.list_bills(demo_engine, hh))
    assert repository.account_name_map(demo_engine, hh)[account.id] == "Family checking"
    spends = repository.top_spending_merchants(
        demo_engine,
        hh,
        __import__("datetime").date.today() - __import__("datetime").timedelta(days=1),
        __import__("datetime").date.today() + __import__("datetime").timedelta(days=1),
        "USD",
    )
    assert any(m.merchant == "Corner Coffee Shop" and m.amount_minor == 4200 for m in spends)

    # At rest, every one of those strings is ciphertext.
    with demo_engine.connect() as conn:
        raw_txn = conn.execute(
            sql_text("select merchant, description, note from transactions where id = :i"),
            {"i": txn_id},
        ).one()
        raw_account = conn.execute(
            sql_text("select name from accounts where id = :i"), {"i": account.id}
        ).scalar_one()
        raw_bill = conn.execute(
            sql_text("select name from bills where id = :i"), {"i": bill.id}
        ).scalar_one()
    for value in [*raw_txn, raw_account, raw_bill]:
        assert value.startswith(household_crypto.ENC_PREFIX)
    joined = " ".join([*raw_txn, raw_account, raw_bill])
    for word in ["Coffee", "scone", "grandma", "checking", "Metro"]:
        assert word not in joined


def test_wraps_recovery_and_rotation(_master_key, demo_engine) -> None:
    """Phase 2: member wraps mint where passwords are proven, the recovery key
    unwraps the DEK, and rotation re-encrypts rows, drops member+recovery
    wraps, and re-wraps devices from their stored public keys."""
    hh = repository.list_households(demo_engine)[0]
    member = repository.list_members(demo_engine, hh)[0]

    # A memory sealed under the CURRENT key, to survive rotation.
    repository.upsert_household_memory(demo_engine, hh, "pet", "a golden retriever")

    household_crypto.ensure_member_wrap(demo_engine, hh, member.user_id, "correct horse battery")
    secret = household_crypto.generate_recovery_key(demo_engine, hh)
    assert secret and secret.startswith("FCFO-")

    status = household_crypto.wrap_status(demo_engine, hh)
    assert status["member_wraps"] == 1
    assert status["has_recovery_key"] is True

    # The recovery secret and the member password both unwrap the same DEK.
    with demo_engine.connect() as conn:
        wraps = {
            row.kind: row.wrap_json
            for row in conn.execute(select(models.household_key_wraps)).all()
        }
    dek_via_password = household_crypto.unwrap_with_password(
        wraps["member"], "correct horse battery"
    )
    dek_via_recovery = household_crypto.unwrap_with_password(wraps["recovery"], secret)
    assert dek_via_password is not None
    assert dek_via_password == dek_via_recovery
    assert household_crypto.unwrap_with_password(wraps["member"], "wrong password") is None

    # Rotation: rows stay readable, member+recovery wraps drop.
    assert household_crypto.rotate_household_key(demo_engine, hh) is True
    memories = repository.list_household_memories(demo_engine, hh)
    assert any(m.value == "a golden retriever" for m in memories)
    status = household_crypto.wrap_status(demo_engine, hh)
    assert status["member_wraps"] == 0
    assert status["has_recovery_key"] is False
    # The old wraps' DEK no longer decrypts anything: the sealed row now uses
    # the rotated key.
    with demo_engine.connect() as conn:
        raw = conn.execute(
            sql_text("select value from household_memories where key = 'pet'")
        ).scalar_one()
    old_rows = household_crypto._subkey_fernet(dek_via_password, b"rows")
    with pytest.raises(Exception):
        old_rows.decrypt(raw[len(household_crypto.ENC_PREFIX):].encode())


@pytest.mark.anyio
async def test_key_status_and_recovery_endpoints(_master_key, demo_client, demo_token) -> None:
    """Through the ROUTE, not the helper — the helper's dict shape and the
    response schema drifted once (enabled vs encryption_enabled) and only an
    endpoint test can catch that class of bug."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    status = await demo_client.get("/api/v1/household/key-status", headers=headers)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["encryption_enabled"] is True
    assert body["has_recovery_key"] is False

    minted = await demo_client.post("/api/v1/household/recovery-key", headers=headers)
    assert minted.status_code == 200, minted.text
    assert minted.json()["recovery_key"].startswith("FCFO-")

    status = await demo_client.get("/api/v1/household/key-status", headers=headers)
    assert status.json()["has_recovery_key"] is True
