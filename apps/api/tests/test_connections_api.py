import pytest

from family_cfo_api import banksync
from family_cfo_api.api import connections as connections_module


class _StubConnector:
    def claim(self, setup_token: str) -> str:
        if setup_token == "bad":
            raise banksync.BankSyncError("that does not look like a SimpleFIN setup token")
        return "https://u:p@bridge.example/simplefin"

    def fetch_accounts(self, access_url, since):
        return []  # the initial background sync no-ops in these tests


@pytest.fixture(autouse=True)
def _stub_connector(monkeypatch):
    monkeypatch.setattr(connections_module.banksync, "SimpleFINConnector", _StubConnector)


async def _create(client, token, name="My Bank"):
    return await client.post(
        "/api/v1/connections",
        headers={"Authorization": f"Bearer {token}"},
        json={"display_name": name, "setup_token": "Z29vZC10b2tlbg=="},
    )


@pytest.mark.anyio
async def test_create_list_delete_connection(demo_client, demo_token) -> None:
    created = await _create(demo_client, demo_token)
    assert created.status_code == 201
    body = created.json()
    assert body["display_name"] == "My Bank"
    # The credential never appears anywhere in the response.
    assert "access_url" not in str(body)
    assert "bridge.example" not in str(body)

    listed = await demo_client.get(
        "/api/v1/connections", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert len(listed.json()["connections"]) == 1

    deleted = await demo_client.delete(
        f"/api/v1/connections/{body['id']}", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert deleted.status_code == 204
    listed = await demo_client.get(
        "/api/v1/connections", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert listed.json()["connections"] == []


@pytest.mark.anyio
async def test_create_rejects_bad_token_and_viewer_role(
    demo_client, demo_token, demo_viewer_token
) -> None:
    bad = await demo_client.post(
        "/api/v1/connections",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"display_name": "X", "setup_token": "bad"},
    )
    assert bad.status_code == 422

    forbidden = await _create(demo_client, demo_viewer_token)
    assert forbidden.status_code == 403


@pytest.mark.anyio
async def test_sync_returns_counts(demo_client, demo_token, monkeypatch) -> None:
    created = await _create(demo_client, demo_token)
    connection_id = created.json()["id"]

    monkeypatch.setattr(
        connections_module.banksync,
        "sync_connection",
        lambda engine, settings, record: banksync.SyncResult(
            accounts_synced=2, imported=14, duplicates_skipped=3
        ),
    )
    resp = await demo_client.post(
        f"/api/v1/connections/{connection_id}/sync",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "accounts_synced": 2,
        "imported": 14,
        "duplicates_skipped": 3,
        "transfers_filed": 0,
        "auto_categorized": 0,
    }


@pytest.mark.anyio
async def test_sync_all_connections_aggregates(demo_client, demo_token, monkeypatch) -> None:
    await _create(demo_client, demo_token, name="Bank A")
    await _create(demo_client, demo_token, name="Bank B")

    monkeypatch.setattr(
        connections_module.banksync,
        "sync_connection",
        lambda engine, settings, record: banksync.SyncResult(
            accounts_synced=1, imported=5, duplicates_skipped=2
        ),
    )
    resp = await demo_client.post(
        "/api/v1/connections/sync",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Two connections, each syncing 1 account / 5 imported / 2 dupes -> summed.
    assert body["accounts_synced"] == 2
    assert body["imported"] == 10
    assert body["duplicates_skipped"] == 4


@pytest.mark.anyio
async def test_pull_to_refresh_always_syncs(demo_client, demo_token, monkeypatch) -> None:
    """M107/ADR 0019: a user pull-to-refresh is an explicit 'fetch now', so it hits
    the provider every time — even right after linking already synced. Only the
    automatic daily poller is throttled, not this."""
    await _create(demo_client, demo_token, name="Bank A")

    called = {"n": 0}

    def spy(engine, settings, record):
        called["n"] += 1
        return banksync.SyncResult(accounts_synced=1, imported=5, duplicates_skipped=2)

    monkeypatch.setattr(connections_module.banksync, "sync_connection", spy)
    resp = await demo_client.post(
        "/api/v1/connections/sync",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert resp.status_code == 200
    # Synced despite the connection having just been synced at link time — no throttle.
    assert called["n"] == 1
    assert resp.json()["imported"] == 5


@pytest.mark.anyio
async def test_sync_unknown_connection_404(demo_client, demo_token) -> None:
    resp = await demo_client.post(
        "/api/v1/connections/77777777-7777-7777-7777-777777777777/sync",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_linking_triggers_an_immediate_background_sync(
    demo_client, demo_token, monkeypatch
) -> None:
    calls = []
    monkeypatch.setattr(
        connections_module.banksync,
        "sync_connection",
        lambda engine, settings, record: calls.append(record.id)
        or banksync.SyncResult(accounts_synced=1, imported=3, duplicates_skipped=0),
    )
    created = await _create(demo_client, demo_token, name="Auto Bank")
    assert created.status_code == 201
    # httpx's ASGI transport runs FastAPI background tasks before returning.
    assert calls == [created.json()["id"]]
