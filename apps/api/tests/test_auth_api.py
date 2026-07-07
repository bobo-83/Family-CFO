import pytest

from family_cfo_api import fixtures


@pytest.mark.anyio
async def test_login_with_correct_credentials_returns_session(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["household_id"] == fixtures.DEMO_HOUSEHOLD_ID
    assert body["user_id"] == fixtures.DEMO_USER_ID
    assert body["role"] == "owner"
    assert body["access_token"]


@pytest.mark.anyio
async def test_login_with_wrong_password_returns_401(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"]


@pytest.mark.anyio
async def test_login_with_unknown_email_returns_401(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": "nobody@example.com", "password": "whatever123"},
    )

    assert response.status_code == 401
