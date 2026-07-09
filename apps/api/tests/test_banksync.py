import base64
from datetime import date

import httpx
import pytest
from sqlalchemy.engine import Engine

from family_cfo_api import banksync, fixtures, repository
from family_cfo_api.config import Settings

_KEY = "jNM8CH53WkD3XZ3P8FluvPFI6BuGGvDIzy6vwiu3jbY="
_HH = fixtures.DEMO_HOUSEHOLD_ID


def _settings() -> Settings:
    return Settings(version="0.1.0", health_check_database=False, backup_encryption_key=_KEY)


def _connector(handler) -> banksync.SimpleFINConnector:
    return banksync.SimpleFINConnector(client=httpx.Client(transport=httpx.MockTransport(handler)))


def _accounts_payload():
    return {
        "accounts": [
            {
                "id": "ext-checking-1",
                "name": "Everyday Checking",
                "currency": "USD",
                "balance": "1250.55",
                "transactions": [
                    {"id": "t-1", "posted": 1751932800, "amount": "-42.50", "payee": "Grocery"},
                    {"id": "t-2", "posted": 1751932800, "amount": "-42.50", "payee": "Grocery"},
                ],
            }
        ]
    }


def test_claim_exchanges_setup_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        return httpx.Response(200, text="https://user:pass@bridge.example/simplefin")

    token = base64.b64encode(b"https://bridge.example/claim/abc").decode()
    assert _connector(handler).claim(token).startswith("https://user:pass@")


def test_claim_rejects_garbage_tokens() -> None:
    connector = _connector(lambda r: httpx.Response(500))
    with pytest.raises(banksync.BankSyncError):
        connector.claim("not-base64!!!")
    with pytest.raises(banksync.BankSyncError):
        connector.claim(base64.b64encode(b"http://insecure/claim").decode())


def test_credential_roundtrip_and_missing_key() -> None:
    settings = _settings()
    token = banksync.encrypt_credential(settings, "https://secret@bridge/simplefin")
    assert "secret" not in token
    assert banksync.decrypt_credential(settings, token) == "https://secret@bridge/simplefin"

    with pytest.raises(banksync.BankSyncError):
        banksync.encrypt_credential(Settings(backup_encryption_key=None), "x")


def _linked_connection(engine: Engine, settings: Settings) -> repository.InstitutionConnectionRecord:
    return repository.create_institution_connection(
        engine,
        household_id=_HH,
        provider="simplefin",
        display_name="Test Bank",
        access_url_encrypted=banksync.encrypt_credential(settings, "https://u:p@bridge/simplefin"),
    )


def test_sync_imports_and_is_idempotent(demo_engine: Engine) -> None:
    settings = _settings()
    connection = _linked_connection(demo_engine, settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_accounts_payload())

    connector = _connector(handler)
    first = banksync.sync_connection(demo_engine, settings, connection, connector)
    # Two same-day same-amount same-payee rows survive: different provider ids.
    assert first.imported == 2 and first.duplicates_skipped == 0
    assert first.accounts_synced == 1

    # Re-sync the same window: provider ids make it a no-op.
    connection = repository.get_institution_connection(demo_engine, _HH, connection.id)
    second = banksync.sync_connection(demo_engine, settings, connection, connector)
    assert second.imported == 0 and second.duplicates_skipped == 2

    txns = [
        t
        for t in repository.list_transactions(demo_engine, _HH)
        if t.merchant == "Grocery"
    ]
    assert len(txns) == 2


def test_sync_auto_creates_and_reuses_the_mapped_account(demo_engine: Engine) -> None:
    settings = _settings()
    connection = _linked_connection(demo_engine, settings)
    connector = _connector(lambda r: httpx.Response(200, json=_accounts_payload()))

    banksync.sync_connection(demo_engine, settings, connection, connector)
    names = [b.name for b in repository.list_account_balances(demo_engine, _HH)]
    assert names.count("Everyday Checking") == 1

    banksync.sync_connection(demo_engine, settings, connection, connector)
    names = [b.name for b in repository.list_account_balances(demo_engine, _HH)]
    assert names.count("Everyday Checking") == 1  # mapping reused, not duplicated


def test_sync_failure_records_error_and_raises(demo_engine: Engine) -> None:
    settings = _settings()
    connection = _linked_connection(demo_engine, settings)
    connector = _connector(lambda r: httpx.Response(500))

    with pytest.raises(banksync.BankSyncError):
        banksync.sync_connection(demo_engine, settings, connection, connector)
    refreshed = repository.get_institution_connection(demo_engine, _HH, connection.id)
    assert refreshed.last_sync_error is not None


def test_hash_dedupe_fallback_without_provider_ids(demo_engine: Engine) -> None:
    account = repository.create_account(demo_engine, _HH, "CSV Acct", "checking", "USD")
    kwargs = dict(
        engine=demo_engine,
        household_id=_HH,
        account_id=account.id,
        occurred_at=date(2026, 7, 1),
        amount_minor=-4250,
        currency="USD",
        merchant="Coffee Shop",
        description=None,
        import_source="csv",
    )
    assert repository.create_transaction_deduped(**kwargs) is True
    assert repository.create_transaction_deduped(**kwargs) is False  # hash match skipped
