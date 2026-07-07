import pytest


@pytest.mark.anyio
async def test_get_household_context_requires_authentication(demo_client) -> None:
    response = await demo_client.get("/api/v1/household")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_get_household_context_returns_computed_summary(demo_client, demo_token) -> None:
    response = await demo_client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {demo_token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["currency"] == "USD"
    assert body["net_worth"] == {"amount_minor": -298_000_000, "currency": "USD"}
    assert body["emergency_fund_months"] == pytest.approx(2_000_000 / 208_000)
