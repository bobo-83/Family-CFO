"""#63: the worker's own money-moving writes leave an audit trail.

Bank sync, bulk auto-filing, statement parsing and key rotation all mutate
balances, categories or access without anyone pressing a button. Each records ONE
row per operation — never one per transaction — whose summary names the CAUSE and
carries the counts, and whose actor is NULL when nobody asked for it.
"""

from datetime import date

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.engine import Engine

from family_cfo_api import (
    banksync,
    finance_service,
    fixtures,
    household_crypto,
    import_processing,
    models,
    repository,
    undo_actions,
)
from family_cfo_api.config import Settings, get_settings
from tests._test_keys import TEST_FERNET_KEY

_HH = fixtures.DEMO_HOUSEHOLD_ID


def _settings() -> Settings:
    return Settings(
        version="0.1.0", health_check_database=False, backup_encryption_key=TEST_FERNET_KEY
    )


def _rows(engine: Engine, action: str | None = None) -> list[repository.AuditEventRecord]:
    records = repository.list_audit_events(engine, _HH)
    return [r for r in records if action is None or r.action == action]


def _accounts_payload(transaction_count: int) -> dict:
    return {
        "accounts": [
            {
                "id": "ext-checking-1",
                "name": "Everyday Checking",
                "currency": "USD",
                "balance": "1250.55",
                "transactions": [
                    {
                        "id": f"t-{index}",
                        "posted": 1751932800,
                        "amount": "-4.50",
                        "payee": f"Merchant {index}",
                    }
                    for index in range(transaction_count)
                ],
            }
        ]
    }


# Bound before any monkeypatch swaps the name out from under the helper.
_REAL_CONNECTOR = banksync.SimpleFINConnector


def _connector(transaction_count: int) -> banksync.SimpleFINConnector:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_accounts_payload(transaction_count))

    return _REAL_CONNECTOR(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _linked_connection(
    engine: Engine, settings: Settings, name: str = "Test Bank"
) -> repository.InstitutionConnectionRecord:
    return repository.create_institution_connection(
        engine,
        household_id=_HH,
        provider="simplefin",
        display_name=name,
        access_url_encrypted=banksync.encrypt_credential(settings, "https://u:p@bridge/simplefin"),
    )


# --- bank sync ---------------------------------------------------------------


def test_the_scheduled_worker_sync_audits_with_counts_and_no_actor(
    demo_engine: Engine, monkeypatch
) -> None:
    """The nightly poller's sync is nobody's action: the row names the cause and
    leaves the actor NULL, rather than blaming whoever linked the account."""
    settings = _settings()
    _linked_connection(demo_engine, settings, name="Test Bank")
    monkeypatch.setattr(banksync, "SimpleFINConnector", lambda: _connector(3))

    assert banksync.sync_due_connections(demo_engine, settings) == {_HH}

    rows = _rows(demo_engine, "connection.synced")
    assert len(rows) == 1
    assert rows[0].actor_user_id is None
    assert "Scheduled sync of “Test Bank”" in rows[0].summary
    assert "1 accounts" in rows[0].summary
    assert "1 balances recorded" in rows[0].summary
    assert "3 new transactions" in rows[0].summary
    assert "0 already-known skipped" in rows[0].summary


def test_a_large_sync_writes_one_row_not_one_per_transaction(
    demo_engine: Engine, monkeypatch
) -> None:
    """A 400-row sync must leave a trail a human can read: one row with the
    numbers, not 400 rows nobody will ever scroll through."""
    settings = _settings()
    _linked_connection(demo_engine, settings)
    monkeypatch.setattr(banksync, "SimpleFINConnector", lambda: _connector(400))

    banksync.sync_due_connections(demo_engine, settings)

    assert len(repository.list_transactions(demo_engine, _HH, limit=100_000)) >= 400
    assert len(_rows(demo_engine, "connection.synced")) == 1
    assert "400 new transactions" in _rows(demo_engine, "connection.synced")[0].summary


def test_a_resync_records_what_it_skipped(demo_engine: Engine) -> None:
    settings = _settings()
    connection = _linked_connection(demo_engine, settings)
    connector = _connector(2)

    banksync.sync_connection(demo_engine, settings, connection, connector)
    connection = repository.get_institution_connection(demo_engine, _HH, connection.id)
    banksync.sync_connection(demo_engine, settings, connection, connector)

    summaries = [r.summary for r in _rows(demo_engine, "connection.synced")]
    assert len(summaries) == 2
    assert any("0 new transactions, 2 already-known skipped" in s for s in summaries)


@pytest.mark.anyio
async def test_a_member_requested_sync_is_attributed_to_that_member(
    demo_client, demo_engine: Engine, demo_token, demo_settings, monkeypatch
) -> None:
    """Pull-to-refresh IS somebody's action, so it carries their id — the NULL
    actor is reserved for work nobody asked for."""
    connection = _linked_connection(demo_engine, demo_settings, name="Requested Bank")
    monkeypatch.setattr(banksync, "SimpleFINConnector", lambda: _connector(2))

    response = await demo_client.post(
        f"/api/v1/connections/{connection.id}/sync",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert response.status_code == 200

    rows = _rows(demo_engine, "connection.synced")
    assert len(rows) == 1
    assert rows[0].actor_user_id == fixtures.DEMO_USER_ID
    assert "Requested sync of “Requested Bank”" in rows[0].summary


def test_a_failed_sync_writes_no_row(demo_engine: Engine, monkeypatch) -> None:
    settings = _settings()
    connection = _linked_connection(demo_engine, settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    failing = banksync.SimpleFINConnector(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(banksync.BankSyncError):
        banksync.sync_connection(demo_engine, settings, connection, failing)
    assert _rows(demo_engine, "connection.synced") == []


# --- bulk auto-filing --------------------------------------------------------


def _uncategorized_ids(engine: Engine) -> set[str]:
    return {
        t.id
        for t in repository.list_transactions(engine, _HH, limit=100_000)
        if t.category_id is None
    }


def _seed_transfers(engine: Engine, count: int) -> None:
    repository.create_category(engine, _HH, "Transfers")
    for index in range(count):
        repository.create_transaction(
            engine,
            household_id=_HH,
            account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
            occurred_at=date(2026, 3, 1 + index),
            amount_minor=-25_000,
            currency="USD",
            merchant="Online Transfer to Savings",
            description=None,
            import_source="bank_sync",
            import_id=None,
            review_state="reviewed",
        )


def test_bulk_autofile_audits_the_count_with_no_actor(demo_engine: Engine) -> None:
    _seed_transfers(demo_engine, 5)
    before = _uncategorized_ids(demo_engine)

    finance_service.autofile_all(demo_engine, _HH)

    filed = before - _uncategorized_ids(demo_engine)
    assert len(filed) >= 5  # the seeded transfers, at least
    rows = _rows(demo_engine, "transactions.auto_filed")
    assert len(rows) == 1  # one row for the run, not one per transaction
    assert rows[0].actor_user_id is None
    assert f"Auto-filed {len(filed)} transactions after sync" in rows[0].summary
    assert "transfers" in rows[0].summary


def test_autofile_is_undoable_and_only_clears_what_it_filed(demo_engine: Engine) -> None:
    _seed_transfers(demo_engine, 3)
    # A transaction the household categorized by hand must survive the undo.
    manual = repository.create_transaction(
        demo_engine,
        household_id=_HH,
        account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
        occurred_at=date(2026, 3, 20),
        amount_minor=-1_200,
        currency="USD",
        merchant="Corner Bakery",
        description=None,
        import_source="csv",
        import_id=None,
        review_state="reviewed",
        category_id=fixtures.DEMO_GROCERIES_CATEGORY_ID,
    )
    before = _uncategorized_ids(demo_engine)

    finance_service.autofile_all(demo_engine, _HH)
    filed = before - _uncategorized_ids(demo_engine)
    assert filed

    row = _rows(demo_engine, "transactions.auto_filed")[0]
    assert row.undo_token is not None
    import json

    undo_actions.reverse(demo_engine, _HH, json.loads(row.undo_token))

    assert filed <= _uncategorized_ids(demo_engine)  # every filed row is blank again
    restored = repository.get_transaction(demo_engine, _HH, manual)
    assert restored is not None
    assert restored.category_id == fixtures.DEMO_GROCERIES_CATEGORY_ID


def test_autofile_that_changes_nothing_writes_no_row(demo_engine: Engine) -> None:
    finance_service.autofile_all(demo_engine, _HH)
    first = len(_rows(demo_engine, "transactions.auto_filed"))
    finance_service.autofile_all(demo_engine, _HH)  # nothing left to file
    assert len(_rows(demo_engine, "transactions.auto_filed")) == first


def test_an_auto_created_taxes_category_is_audited(demo_engine: Engine) -> None:
    repository.create_transaction(
        demo_engine,
        household_id=_HH,
        account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
        occurred_at=date(2026, 3, 5),
        amount_minor=-80_000,
        currency="USD",
        merchant="Gencash Trade Lapse",
        description=None,
        import_source="bank_sync",
        import_id=None,
        review_state="reviewed",
    )
    finance_service.autofile_all(demo_engine, _HH)

    rows = [r for r in _rows(demo_engine, "category.created") if "Taxes" in r.summary]
    assert len(rows) == 1
    assert rows[0].actor_user_id is None
    assert "auto-file tax withholding" in rows[0].summary


# --- statement parsing in the worker ----------------------------------------


def _loan_statement(engine: Engine) -> repository.ImportRecord:
    loan = repository.create_account(
        engine,
        _HH,
        name="Education Loan Servicer",
        account_type="student_loan",
        currency="USD",
    )
    return repository.create_import(
        engine,
        household_id=_HH,
        account_id=loan.id,
        source_type="pdf",
        filename="statement.pdf",
    )


def test_statement_parsing_audits_the_account_and_balance_it_rewrites(
    demo_engine: Engine,
) -> None:
    import_record = _loan_statement(demo_engine)
    text = (
        "Statement Closing Date: 07/10/2026\n"
        "New Balance: $9,500.00\n"
        "Minimum Payment Due: $81.53\n"
        "Payment Due Date: 08/08/2026\n"
    )

    import_processing.apply_statement_fields_to_account(demo_engine, import_record, text)

    updated = _rows(demo_engine, "account.updated")
    balances = _rows(demo_engine, "account.balance_recorded")
    assert len(updated) == 1 and len(balances) == 1
    assert updated[0].actor_user_id is None and balances[0].actor_user_id is None
    assert "Statement import updated the minimum payment and payment due date" in (
        updated[0].summary
    )
    assert "Statement import recorded a new balance" in balances[0].summary
    # Audit rows are Internal: they say WHICH field moved, never the figure.
    blob = updated[0].summary + balances[0].summary
    assert "81.53" not in blob and "9,500" not in blob


def test_reimporting_the_same_statement_does_not_repeat_the_row(demo_engine: Engine) -> None:
    """The parse is idempotent, so the trail must be too — a re-upload that moves
    nothing writes nothing."""
    import_record = _loan_statement(demo_engine)
    text = (
        "Statement Closing Date: 07/10/2026\n"
        "New Balance: $9,500.00\n"
        "Minimum Payment Due: $81.53\n"
        "Payment Due Date: 08/08/2026\n"
    )
    import_processing.apply_statement_fields_to_account(demo_engine, import_record, text)
    import_processing.apply_statement_fields_to_account(demo_engine, import_record, text)

    assert len(_rows(demo_engine, "account.updated")) == 1
    assert len(_rows(demo_engine, "account.balance_recorded")) == 1


def test_a_statement_with_nothing_to_apply_writes_no_row(demo_engine: Engine) -> None:
    import_record = _loan_statement(demo_engine)
    import_processing.apply_statement_fields_to_account(
        demo_engine, import_record, "Coffee Shop  $4.50\nThanks!"
    )
    assert _rows(demo_engine, "account.updated") == []
    assert _rows(demo_engine, "account.balance_recorded") == []


# --- household key rotation --------------------------------------------------


@pytest.fixture
def _master_key(monkeypatch):
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


def test_key_rotation_is_audited_and_says_the_recovery_key_is_gone(
    _master_key, demo_engine: Engine
) -> None:
    """UNDO_POLICY has reserved ``household.key_rotated`` since ADR 0023 and it was
    never emitted — so the invalidated recovery key was an invisible consequence."""
    assert household_crypto.rotate_household_key(demo_engine, _HH, fixtures.DEMO_USER_ID)

    rows = _rows(demo_engine, "household.key_rotated")
    assert len(rows) == 1
    assert rows[0].actor_user_id == fixtures.DEMO_USER_ID
    assert "recovery key was invalidated" in rows[0].summary
    assert rows[0].undo_token is None  # key material is gone; nothing to restore


@pytest.mark.anyio
async def test_removing_a_member_records_the_rotation_it_causes(
    _master_key, demo_client, demo_engine: Engine, demo_token
) -> None:
    created = await demo_client.post(
        "/api/v1/household/members",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "email": "adult2@example.com",
            "password": "another-password-1",
            "display_name": "Adult Two",
            "role": "adult",
        },
    )
    assert created.status_code == 201
    user_id = created.json()["user_id"]

    removed = await demo_client.delete(
        f"/api/v1/household/members/{user_id}",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert removed.status_code == 204

    with demo_engine.connect() as conn:
        actions = [r[0] for r in conn.execute(select(models.audit_events.c.action)).all()]
    assert "household.key_rotated" in actions
    assert "member.removed" in actions
