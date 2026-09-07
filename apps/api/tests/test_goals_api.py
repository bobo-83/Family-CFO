import pytest


@pytest.mark.anyio
async def test_owner_can_create_goal(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/goals",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "name": "New car",
            "type": "vehicle",
            "target": {"amount_minor": 2_000_000, "currency": "USD"},
            "priority": 2,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "New car"
    assert body["current"] == {"amount_minor": 0, "currency": "USD"}


@pytest.mark.anyio
async def test_viewer_cannot_create_goal(demo_client, demo_viewer_token) -> None:
    response = await demo_client.post(
        "/api/v1/goals",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
        json={
            "name": "New car",
            "type": "vehicle",
            "target": {"amount_minor": 2_000_000, "currency": "USD"},
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"]


@pytest.mark.anyio
async def test_create_goal_requires_authentication(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/goals",
        json={
            "name": "New car",
            "type": "vehicle",
            "target": {"amount_minor": 1, "currency": "USD"},
        },
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_goals_and_the_top_goal_card_survive_a_foreign_currency_account(
    demo_client, demo_token, demo_engine, foreign_currency_account
) -> None:
    """#152: an emergency-fund goal reads the fund through emergency_fund_inputs,
    which raised — taking GET /goals and the Overview's top-goal card down with
    the home screen. The demo fixture has such a goal."""
    from family_cfo_api import fixtures, repository

    headers = {"Authorization": f"Bearer {demo_token}"}
    hh = fixtures.DEMO_HOUSEHOLD_ID
    savings = next(b for b in repository.list_account_balances(demo_engine, hh) if b.name == "Savings")
    repository.update_account(demo_engine, hh, savings.account_id, emergency_fund_percent=50.0)
    # A designation on the EUR account cannot join a USD fund: ignored, and said so.
    repository.update_account(
        demo_engine, hh, foreign_currency_account.id, emergency_fund_percent=100.0
    )

    goals = await demo_client.get("/api/v1/goals", headers=headers)
    assert goals.status_code == 200, goals.text
    emergency = next(g for g in goals.json()["goals"] if g["type"] == "emergency_fund")
    assert emergency["current"] == {"amount_minor": 750_000, "currency": "USD"}

    overview = await demo_client.get("/api/v1/household", headers=headers)
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["top_goal"]["type"] == "emergency_fund"
    assert body["top_goal"]["current"] == {"amount_minor": 750_000, "currency": "USD"}
    assert body["emergency_fund"]["reserved"] == {"amount_minor": 750_000, "currency": "USD"}
    assert (
        "An emergency-fund designation on 1 account held in EUR is ignored; designations "
        "count only in USD."
    ) in body["safe_to_spend"]["warnings"]
