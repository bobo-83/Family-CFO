import pytest

from family_cfo_api import fixtures


@pytest.mark.anyio
async def test_household_bootstrap_returns_working_session(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/households",
        json={
            "display_name": "Bootstrapped Home",
            "base_currency": "USD",
            "owner_email": "boot@example.com",
            "owner_password": "password-123",
            "owner_display_name": "Boot Owner",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "owner"
    token = body["access_token"]

    # The returned session immediately works against a protected route.
    household = await demo_client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {token}"}
    )
    assert household.status_code == 200
    assert household.json()["display_name"] == "Bootstrapped Home"


@pytest.mark.anyio
async def test_household_bootstrap_rejects_duplicate_email(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/households",
        json={
            "display_name": "Dupe",
            "base_currency": "USD",
            "owner_email": fixtures.DEMO_USER_EMAIL,
            "owner_password": "password-123",
            "owner_display_name": "Dupe Owner",
        },
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_account_crud_and_balance(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    created = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Brokerage", "type": "brokerage", "currency": "USD"},
    )
    assert created.status_code == 201
    account_id = created.json()["id"]
    assert created.json()["balance"]["amount_minor"] == 0

    balance = await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": 500000, "currency": "USD"}},
    )
    assert balance.status_code == 201
    assert balance.json()["balance"]["amount_minor"] == 500000

    updated = await demo_client.patch(
        f"/api/v1/accounts/{account_id}", headers=headers, json={"name": "Renamed"}
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"

    deleted = await demo_client.delete(f"/api/v1/accounts/{account_id}", headers=headers)
    assert deleted.status_code == 204


@pytest.mark.anyio
async def test_delete_account_in_use_returns_409(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    response = await demo_client.delete(
        f"/api/v1/accounts/{fixtures.DEMO_CHECKING_ACCOUNT_ID}", headers=headers
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_transaction_crud(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    created = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": fixtures.DEMO_CHECKING_ACCOUNT_ID,
            "occurred_at": "2026-03-01",
            "amount": {"amount_minor": -2500, "currency": "USD"},
            "merchant": "Cafe",
        },
    )
    assert created.status_code == 201
    txn_id = created.json()["id"]

    updated = await demo_client.patch(
        f"/api/v1/transactions/{txn_id}", headers=headers, json={"merchant": "Bistro"}
    )
    assert updated.status_code == 200
    assert updated.json()["merchant"] == "Bistro"

    deleted = await demo_client.delete(f"/api/v1/transactions/{txn_id}", headers=headers)
    assert deleted.status_code == 204


@pytest.mark.anyio
async def test_transaction_currency_must_match_account(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    response = await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": fixtures.DEMO_CHECKING_ACCOUNT_ID,
            "occurred_at": "2026-03-01",
            "amount": {"amount_minor": -2500, "currency": "EUR"},
        },
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_bill_and_income_crud(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    bill = await demo_client.post(
        "/api/v1/bills",
        headers=headers,
        json={
            "name": "Gym",
            "amount": {"amount_minor": 5000, "currency": "USD"},
            "frequency": "monthly",
        },
    )
    assert bill.status_code == 201
    bill_id = bill.json()["id"]
    assert (
        await demo_client.patch(
            f"/api/v1/bills/{bill_id}", headers=headers, json={"name": "Gym Plus"}
        )
    ).json()["name"] == "Gym Plus"
    assert (
        await demo_client.delete(f"/api/v1/bills/{bill_id}", headers=headers)
    ).status_code == 204

    income = await demo_client.post(
        "/api/v1/income",
        headers=headers,
        json={
            "name": "Freelance",
            "amount": {"amount_minor": 100000, "currency": "USD"},
            "frequency": "monthly",
        },
    )
    assert income.status_code == 201
    income_id = income.json()["id"]
    assert (
        await demo_client.patch(
            f"/api/v1/income/{income_id}", headers=headers, json={"name": "Consulting"}
        )
    ).json()["name"] == "Consulting"
    assert (
        await demo_client.delete(f"/api/v1/income/{income_id}", headers=headers)
    ).status_code == 204


@pytest.mark.anyio
async def test_viewer_cannot_write(demo_client, demo_viewer_token) -> None:
    headers = {"Authorization": f"Bearer {demo_viewer_token}"}
    response = await demo_client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "X", "type": "checking", "currency": "USD"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_member_management_and_last_owner_guard(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    created = await demo_client.post(
        "/api/v1/household/members",
        headers=headers,
        json={
            "email": "adult2@example.com",
            "password": "password-123",
            "display_name": "Adult Two",
            "role": "adult",
        },
    )
    assert created.status_code == 201
    user_id = created.json()["user_id"]

    listed = await demo_client.get("/api/v1/household/members", headers=headers)
    assert any(m["user_id"] == user_id for m in listed.json()["members"])

    promoted = await demo_client.patch(
        f"/api/v1/household/members/{user_id}", headers=headers, json={"role": "viewer"}
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "viewer"

    removed = await demo_client.delete(f"/api/v1/household/members/{user_id}", headers=headers)
    assert removed.status_code == 204

    # The seeded demo owner is the last owner; demoting them is a 409.
    owner_demote = await demo_client.patch(
        f"/api/v1/household/members/{fixtures.DEMO_USER_ID}",
        headers=headers,
        json={"role": "adult"},
    )
    assert owner_demote.status_code == 409


@pytest.mark.anyio
async def test_viewer_cannot_manage_members(demo_client, demo_viewer_token) -> None:
    headers = {"Authorization": f"Bearer {demo_viewer_token}"}
    response = await demo_client.post(
        "/api/v1/household/members",
        headers=headers,
        json={
            "email": "x@example.com",
            "password": "password-123",
            "display_name": "X",
            "role": "adult",
        },
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_audit_log_records_mutations_without_sensitive_values(
    demo_client, demo_token
) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    await demo_client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": fixtures.DEMO_CHECKING_ACCOUNT_ID,
            "occurred_at": "2026-03-01",
            "amount": {"amount_minor": -987654, "currency": "USD"},
            "merchant": "Secret Vendor",
        },
    )

    audit = await demo_client.get("/api/v1/audit", headers=headers)
    assert audit.status_code == 200
    events = audit.json()["events"]
    assert any(e["action"] == "transaction.created" for e in events)
    # No audit summary leaks the raw amount that changed.
    assert all("987654" not in e["summary"] for e in events)


@pytest.mark.anyio
async def test_viewer_cannot_read_audit(demo_client, demo_viewer_token) -> None:
    response = await demo_client.get(
        "/api/v1/audit", headers={"Authorization": f"Bearer {demo_viewer_token}"}
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_bill_can_be_filed_under_a_category(demo_client, demo_token) -> None:
    """M96: a bill can carry a spending category (e.g. Subscriptions), settable
    on create and change/clearable on update, with the name resolved on read."""
    headers = {"Authorization": f"Bearer {demo_token}"}
    cat = await demo_client.post(
        "/api/v1/categories", headers=headers, json={"name": "Subscriptions"})
    cat_id = cat.json()["id"]

    created = await demo_client.post(
        "/api/v1/bills", headers=headers,
        json={
            "name": "Disney+",
            "amount": {"amount_minor": 3299, "currency": "USD"},
            "frequency": "monthly",
            "category_id": cat_id,
        })
    assert created.status_code == 201
    body = created.json()
    assert body["category_id"] == cat_id
    assert body["category_name"] == "Subscriptions"
    bill_id = body["id"]

    # It shows on the list with the name resolved.
    listed = (await demo_client.get("/api/v1/bills", headers=headers)).json()["bills"]
    disney = next(b for b in listed if b["id"] == bill_id)
    assert disney["category_name"] == "Subscriptions"

    # Clearing it (explicit null) removes the category.
    cleared = await demo_client.patch(
        f"/api/v1/bills/{bill_id}", headers=headers, json={"category_id": None})
    assert cleared.json()["category_id"] is None

    # An unrelated update must NOT wipe the category.
    await demo_client.patch(
        f"/api/v1/bills/{bill_id}", headers=headers, json={"category_id": cat_id})
    renamed = await demo_client.patch(
        f"/api/v1/bills/{bill_id}", headers=headers, json={"name": "Disney Plus"})
    assert renamed.json()["category_id"] == cat_id  # untouched by a name-only edit


@pytest.mark.anyio
async def test_bill_with_unknown_category_is_rejected(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    resp = await demo_client.post(
        "/api/v1/bills", headers=headers,
        json={
            "name": "Ghost",
            "amount": {"amount_minor": 100, "currency": "USD"},
            "frequency": "monthly",
            "category_id": "nonexistent",
        })
    assert resp.status_code == 404
