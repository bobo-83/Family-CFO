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


def _conn(last_synced_at, *, cid: str = "c") -> repository.InstitutionConnectionRecord:
    from datetime import datetime, timezone

    return repository.InstitutionConnectionRecord(
        id=cid,
        household_id=_HH,
        provider="simplefin",
        display_name="Test Bank",
        access_url_encrypted="x",
        status="active",
        last_synced_at=last_synced_at,
        last_sync_error=None,
        created_at=datetime.now(timezone.utc),
    )


def test_due_for_sync_is_a_daily_gate() -> None:
    """M107/ADR 0019 regression guard for the AUTOMATIC poller: SimpleFIN refreshes
    ~once/day and rate-limits, so the background sync runs at most once a day per
    connection. (The old bug was a 5-minute poll with no gate ≈ 288 calls/day.)"""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    assert banksync.due_for_sync(_conn(None)) is True
    assert banksync.due_for_sync(_conn(now - timedelta(hours=25))) is True
    # Synced 5 minutes ago — the broken cadence — and anytime within the day: skip.
    assert banksync.due_for_sync(_conn(now - timedelta(minutes=5))) is False
    assert banksync.due_for_sync(_conn(now - timedelta(hours=12))) is False
    # The gate must be a full day (never regress toward a sub-daily poll).
    assert banksync.SCHEDULED_SYNC_INTERVAL >= timedelta(hours=20)


def test_sync_due_connections_syncs_at_most_once_a_day(
    demo_engine: Engine, monkeypatch
) -> None:
    """The scheduled poller must sync a connection at most once a day even though it
    polls hourly — the exact guarantee the original 5-minute job lacked."""
    settings = _settings()
    connection = _linked_connection(demo_engine, settings)  # last_synced_at is None

    calls: list[str] = []

    def spy(engine, settings, conn, connector=None):
        calls.append(conn.id)
        repository.record_connection_sync(engine, conn.id, error=None)  # stamps last_synced_at
        return banksync.SyncResult(accounts_synced=0, imported=0, duplicates_skipped=0)

    monkeypatch.setattr(banksync, "sync_connection", spy)

    banksync.sync_due_connections(demo_engine, settings)  # due (never synced) → syncs
    banksync.sync_due_connections(demo_engine, settings)  # just synced → skipped
    assert calls == [connection.id]  # exactly one provider call within the day


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


def test_sync_always_requests_the_full_history_window(demo_engine: Engine) -> None:
    """M59: every sync asks for ~13 months, even the first one.

    The old behavior sent NO start-date on the first sync (providers then
    return almost nothing) and only since-last-sync afterwards, so a
    household's transaction history was never fetched.
    """
    settings = _settings()
    connection = _linked_connection(demo_engine, settings)
    seen_params: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        return httpx.Response(200, json=_accounts_payload())

    connector = _connector(handler)
    banksync.sync_connection(demo_engine, settings, connection, connector)
    # Sync again with last_synced_at now set: the window must not shrink.
    connection = repository.get_institution_connection(demo_engine, _HH, connection.id)
    assert connection.last_synced_at is not None
    banksync.sync_connection(demo_engine, settings, connection, connector)

    from datetime import datetime, timedelta, timezone

    expected = date.today() - timedelta(days=banksync.SYNC_LOOKBACK_DAYS)
    for params in seen_params:
        assert "start-date" in params
        start = datetime.fromtimestamp(int(params["start-date"]), tz=timezone.utc).date()
        assert start == expected


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


def test_synced_accounts_carry_institution_and_last_synced(demo_engine: Engine) -> None:
    settings = _settings()
    connection = _linked_connection(demo_engine, settings)
    connector = _connector(lambda r: httpx.Response(200, json=_accounts_payload()))
    banksync.sync_connection(demo_engine, settings, connection, connector)

    info = repository.account_connection_map(demo_engine, _HH)
    assert len(info) == 1
    entry = next(iter(info.values()))
    assert entry.institution == "Test Bank"
    assert entry.last_synced_at is not None


# --- M35: account type inference ------------------------------------------------


def test_infer_account_type_covers_common_names() -> None:
    cases = {
        "Acme 401k Plan": "retirement",
        "My 401(k)": "retirement",
        "Roth IRA": "retirement",
        "403(b) Retirement": "retirement",
        "Health Savings Account HSA": "hsa",
        "NY 529 College Savings": "529",
        "Brokerage Account": "brokerage",
        "Investment Account": "brokerage",
        "Home Mortgage": "mortgage",
        "Auto Loan": "auto_loan",
        "Student Loans": "student_loan",
        "Rewards Visa": "credit_card",
        "Platinum Credit Card": "credit_card",
        "High-Yield Savings": "savings",
        "Money Market": "savings",
        "Everyday Checking": "checking",
        "Mystery Account": "checking",
    }
    for name, expected in cases.items():
        assert banksync.infer_account_type(name) == expected, name


def test_sync_creates_401k_as_retirement_and_never_retypes(demo_engine: Engine) -> None:
    settings = _settings()
    connection = _linked_connection(demo_engine, settings)
    payload = {
        "accounts": [
            {"id": "ext-401k", "name": "Acme 401k Plan", "currency": "USD", "balance": "50000.00"}
        ]
    }
    connector = _connector(lambda r: httpx.Response(200, json=payload))

    banksync.sync_connection(demo_engine, settings, connection, connector)
    account = next(
        b for b in repository.list_account_balances(demo_engine, _HH) if b.name == "Acme 401k Plan"
    )
    assert account.account_type == "retirement"

    # A manual correction survives later syncs: the mapping is reused as-is.
    repository.update_account(demo_engine, _HH, account.account_id, account_type="brokerage")
    connection = repository.get_institution_connection(demo_engine, _HH, connection.id)
    banksync.sync_connection(demo_engine, settings, connection, connector)
    account = next(
        b for b in repository.list_account_balances(demo_engine, _HH) if b.name == "Acme 401k Plan"
    )
    assert account.account_type == "brokerage"
