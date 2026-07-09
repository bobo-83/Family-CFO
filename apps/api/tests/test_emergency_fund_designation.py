"""M36: emergency-fund designation across accounts."""

import pytest

from family_cfo_api import repository


def test_reserved_minor_math() -> None:
    # Percent of the balance, round half-up.
    assert repository.emergency_fund_reserved_minor(20.0, None, 100_000) == 20_000
    assert repository.emergency_fund_reserved_minor(33.33, None, 100_00) == 3_333
    # Fixed amount, capped at the balance.
    assert repository.emergency_fund_reserved_minor(None, 50_000, 100_000) == 50_000
    assert repository.emergency_fund_reserved_minor(None, 500_000, 100_000) == 100_000
    # Non-positive balances and no designation reserve nothing.
    assert repository.emergency_fund_reserved_minor(50.0, None, -5_000) == 0
    assert repository.emergency_fund_reserved_minor(None, 10_000, 0) == 0
    assert repository.emergency_fund_reserved_minor(None, None, 100_000) == 0


async def _create_account(demo_client, headers, name: str, balance_minor: int) -> str:
    account_id = (
        await demo_client.post(
            "/api/v1/accounts",
            headers=headers,
            json={"name": name, "type": "savings", "currency": "USD"},
        )
    ).json()["id"]
    await demo_client.post(
        f"/api/v1/accounts/{account_id}/balances",
        headers=headers,
        json={"balance": {"amount_minor": balance_minor, "currency": "USD"}},
    )
    return account_id


@pytest.mark.anyio
async def test_designation_round_trip_and_clear(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    account_id = await _create_account(demo_client, headers, "HY Savings", 1_000_000)

    # Percent designation → derived reservation from the latest balance.
    updated = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"emergency_fund_percent": 25},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["emergency_fund_percent"] == 25
    assert body["emergency_fund_reserved"] == {"amount_minor": 250_000, "currency": "USD"}

    # Switching to a fixed amount clears the percent.
    updated = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"emergency_fund_amount": {"amount_minor": 120_000, "currency": "USD"}},
    )
    body = updated.json()
    assert body["emergency_fund_percent"] is None
    assert body["emergency_fund_amount"] == {"amount_minor": 120_000, "currency": "USD"}
    assert body["emergency_fund_reserved"] == {"amount_minor": 120_000, "currency": "USD"}

    # Clearing removes the designation entirely.
    updated = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={"clear_emergency_fund": True},
    )
    body = updated.json()
    assert body["emergency_fund_percent"] is None
    assert body["emergency_fund_amount"] is None
    assert body["emergency_fund_reserved"] is None


@pytest.mark.anyio
async def test_percent_and_amount_together_is_400(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    account_id = await _create_account(demo_client, headers, "Both", 100_000)
    response = await demo_client.patch(
        f"/api/v1/accounts/{account_id}",
        headers=headers,
        json={
            "emergency_fund_percent": 10,
            "emergency_fund_amount": {"amount_minor": 1_000, "currency": "USD"},
        },
    )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_net_worth_tool_reports_reservation(demo_client, demo_token, demo_engine) -> None:
    from family_cfo_api import ai_tools, fixtures

    headers = {"Authorization": f"Bearer {demo_token}"}
    first = await _create_account(demo_client, headers, "EF One", 600_000)
    second = await _create_account(demo_client, headers, "EF Two", 400_000)
    await demo_client.patch(
        f"/api/v1/accounts/{first}", headers=headers, json={"emergency_fund_percent": 50}
    )
    await demo_client.patch(
        f"/api/v1/accounts/{second}",
        headers=headers,
        json={"emergency_fund_amount": {"amount_minor": 100_000, "currency": "USD"}},
    )

    payload = ai_tools._get_net_worth(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "USD", {})
    # 50% of 6,000.00 + fixed 1,000.00 = 4,000.00 reserved.
    assert payload["emergency_fund_reserved"]["amount_minor"] == 400_000
    assert "emergency" in payload["spendability_note"].lower()
