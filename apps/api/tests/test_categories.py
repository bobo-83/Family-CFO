"""M45: category management + assigning categories to transactions."""

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from family_cfo_api import fixtures

_HH = fixtures.DEMO_HOUSEHOLD_ID


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _enforce_foreign_keys(engine: Engine) -> None:
    """SQLite ignores foreign keys unless asked; Postgres — what the box actually
    runs — never does. Same helper as `test_backups_api.py` (#68): without it the
    suite cannot see a foreign-key failure at all, which is why #76 sat unnoticed.
    """

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    engine.dispose()  # pooled connections opened before the listener predate the pragma


@pytest.mark.anyio
async def test_category_crud_round_trip(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    created = await demo_client.post(
        "/api/v1/categories", headers=headers, json={"name": "Dining"}
    )
    assert created.status_code == 201
    category_id = created.json()["id"]

    listed = (await demo_client.get("/api/v1/categories", headers=headers)).json()["categories"]
    assert any(c["id"] == category_id and c["name"] == "Dining" for c in listed)

    deleted = await demo_client.delete(f"/api/v1/categories/{category_id}", headers=headers)
    assert deleted.status_code == 204
    listed = (await demo_client.get("/api/v1/categories", headers=headers)).json()["categories"]
    assert all(c["id"] != category_id for c in listed)


@pytest.mark.anyio
async def test_category_rename(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    category_id = (
        await demo_client.post("/api/v1/categories", headers=headers, json={"name": "ZZ Alpha"})
    ).json()["id"]

    renamed = await demo_client.patch(
        f"/api/v1/categories/{category_id}", headers=headers, json={"name": "ZZ Alpha Renamed"}
    )
    assert renamed.status_code == 200
    assert renamed.json() == {"id": category_id, "name": "ZZ Alpha Renamed"}

    listed = (await demo_client.get("/api/v1/categories", headers=headers)).json()["categories"]
    assert any(c["id"] == category_id and c["name"] == "ZZ Alpha Renamed" for c in listed)


@pytest.mark.anyio
async def test_rename_to_existing_name_is_409(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    await demo_client.post("/api/v1/categories", headers=headers, json={"name": "ZZ Beta"})
    other = (
        await demo_client.post("/api/v1/categories", headers=headers, json={"name": "ZZ Gamma"})
    ).json()["id"]
    clash = await demo_client.patch(
        f"/api/v1/categories/{other}", headers=headers, json={"name": "ZZ Beta"}
    )
    assert clash.status_code == 409


@pytest.mark.anyio
async def test_viewer_cannot_rename_category(demo_client, demo_token, demo_viewer_token) -> None:
    category_id = (
        await demo_client.post(
            "/api/v1/categories", headers=_headers(demo_token), json={"name": "ZZ Delta"}
        )
    ).json()["id"]
    blocked = await demo_client.patch(
        f"/api/v1/categories/{category_id}",
        headers=_headers(demo_viewer_token),
        json={"name": "Gas"},
    )
    assert blocked.status_code == 403


@pytest.mark.anyio
async def test_duplicate_name_is_409(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    await demo_client.post("/api/v1/categories", headers=headers, json={"name": "Transport"})
    dupe = await demo_client.post(
        "/api/v1/categories", headers=headers, json={"name": "Transport"}
    )
    assert dupe.status_code == 409


@pytest.mark.anyio
async def test_viewer_cannot_create_category(demo_client, demo_viewer_token) -> None:
    response = await demo_client.post(
        "/api/v1/categories", headers=_headers(demo_viewer_token), json={"name": "Nope"}
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_assign_category_on_create_and_update(demo_client, demo_token, demo_engine) -> None:
    from family_cfo_api import repository

    headers = _headers(demo_token)
    category_id = (
        await demo_client.post("/api/v1/categories", headers=headers, json={"name": "Groceries2"})
    ).json()["id"]
    account_id = repository.list_account_balances(demo_engine, _HH)[0].account_id

    # Create with a category.
    created = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": account_id,
            "occurred_at": "2026-07-08",
            "amount": {"amount_minor": -4200, "currency": "USD"},
            "merchant": "Store",
            "category_id": category_id,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["category_id"] == category_id
    assert body["category"] == "Groceries2"

    # Clear the category via update.
    cleared = await demo_client.patch(
        f"/api/v1/transactions/{body['id']}", headers=headers, json={"clear_category": True}
    )
    assert cleared.json()["category_id"] is None


@pytest.mark.anyio
async def test_unknown_category_on_transaction_is_404(demo_client, demo_token, demo_engine) -> None:
    from family_cfo_api import repository

    headers = _headers(demo_token)
    account_id = repository.list_account_balances(demo_engine, _HH)[0].account_id
    response = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": account_id,
            "occurred_at": "2026-07-08",
            "amount": {"amount_minor": -1000, "currency": "USD"},
            "category_id": "does-not-exist",
        },
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_delete_category_uncategorizes_transactions(
    demo_client, demo_token, demo_engine
) -> None:
    from family_cfo_api import repository

    headers = _headers(demo_token)
    category_id = (
        await demo_client.post("/api/v1/categories", headers=headers, json={"name": "Temp"})
    ).json()["id"]
    account_id = repository.list_account_balances(demo_engine, _HH)[0].account_id
    txn_id = (
        await demo_client.post(
            "/api/v1/transactions",
            headers=headers,
            json={
                "account_id": account_id,
                "occurred_at": "2026-07-08",
                "amount": {"amount_minor": -2000, "currency": "USD"},
                "category_id": category_id,
            },
        )
    ).json()["id"]

    await demo_client.delete(f"/api/v1/categories/{category_id}", headers=headers)

    txn = repository.get_transaction(demo_engine, _HH, txn_id)
    assert txn is not None
    assert txn.category_id is None


@pytest.mark.anyio
async def test_delete_category_uncategorizes_bills_with_foreign_keys_enforced(
    demo_file_client, demo_file_token, demo_file_engine
) -> None:
    """#76: `bills.category_id` carries a real foreign key onto the category.
    With the constraint enforced — the database the operator actually runs — the
    delete used to raise `IntegrityError` out of an endpoint that catches
    nothing, so removing a category any bill was filed under returned a 500. The
    bill is now un-filed in the same transaction, so the DELETE has nothing
    pointing at it.
    """
    from family_cfo_api import repository

    _enforce_foreign_keys(demo_file_engine)
    headers = _headers(demo_file_token)
    category_id = (
        await demo_file_client.post(
            "/api/v1/categories", headers=headers, json={"name": "Subscriptions"}
        )
    ).json()["id"]
    bill = repository.create_bill(
        demo_file_engine, _HH, name="Streaming", amount_minor=1299, currency="USD",
        frequency="monthly", category_id=category_id,
    )

    deleted = await demo_file_client.delete(
        f"/api/v1/categories/{category_id}", headers=headers
    )
    assert deleted.status_code == 204, deleted.text

    after = repository.get_bill(demo_file_engine, _HH, bill.id)
    assert after is not None, "the bill must survive its category"
    assert after.category_id is None
