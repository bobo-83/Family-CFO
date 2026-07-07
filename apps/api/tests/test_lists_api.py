import pytest

LIST_ENDPOINTS = [
    ("/api/v1/accounts", "accounts", 3),
    ("/api/v1/transactions", "transactions", 2),
    ("/api/v1/bills", "bills", 2),
    ("/api/v1/income", "income", 1),
    ("/api/v1/goals", "goals", 1),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("path", "key", "expected_count"), LIST_ENDPOINTS)
async def test_list_endpoint_requires_authentication(demo_client, path, key, expected_count) -> None:
    response = await demo_client.get(path)

    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(("path", "key", "expected_count"), LIST_ENDPOINTS)
async def test_list_endpoint_returns_household_scoped_rows(
    demo_client, demo_token, path, key, expected_count
) -> None:
    response = await demo_client.get(path, headers={"Authorization": f"Bearer {demo_token}"})

    assert response.status_code == 200
    body = response.json()
    assert key in body
    assert len(body[key]) == expected_count


@pytest.mark.anyio
async def test_accounts_include_money_shaped_balances(demo_client, demo_token) -> None:
    response = await demo_client.get(
        "/api/v1/accounts", headers={"Authorization": f"Bearer {demo_token}"}
    )

    account = response.json()["accounts"][0]
    assert set(account["balance"].keys()) == {"amount_minor", "currency"}
