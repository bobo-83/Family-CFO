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
        json={"name": "New car", "type": "vehicle", "target": {"amount_minor": 1, "currency": "USD"}},
    )

    assert response.status_code == 401
