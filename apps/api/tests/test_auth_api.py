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


async def _login(demo_client) -> str:
    response = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD},
    )
    return response.json()["access_token"]


@pytest.mark.anyio
async def test_logout_revokes_the_current_token(demo_client) -> None:
    token = await _login(demo_client)
    headers = {"Authorization": f"Bearer {token}"}

    # The token works before logout.
    assert (await demo_client.get("/api/v1/household", headers=headers)).status_code == 200

    logout = await demo_client.delete("/api/v1/auth/sessions", headers=headers)
    assert logout.status_code == 204

    # ...and is rejected afterwards.
    assert (await demo_client.get("/api/v1/household", headers=headers)).status_code == 401


@pytest.mark.anyio
async def test_refresh_rotates_the_token_and_invalidates_the_old_one(demo_client) -> None:
    old_token = await _login(demo_client)
    old_headers = {"Authorization": f"Bearer {old_token}"}

    refresh = await demo_client.post("/api/v1/auth/sessions/refresh", headers=old_headers)
    assert refresh.status_code == 200
    new_token = refresh.json()["access_token"]
    assert new_token != old_token

    # The new token works; the old one no longer does.
    assert (
        await demo_client.get("/api/v1/household", headers={"Authorization": f"Bearer {new_token}"})
    ).status_code == 200
    assert (await demo_client.get("/api/v1/household", headers=old_headers)).status_code == 401


@pytest.mark.anyio
async def test_expired_session_is_rejected(demo_client, demo_engine) -> None:
    from datetime import timedelta

    from family_cfo_api import models, repository, security
    from sqlalchemy import insert

    token = security.generate_access_token()
    with demo_engine.begin() as conn:
        conn.execute(
            insert(models.auth_sessions).values(
                id=repository.new_id(),
                user_id=fixtures.DEMO_USER_ID,
                household_id=fixtures.DEMO_HOUSEHOLD_ID,
                device_id=None,
                token_hash=security.hash_token(token),
                created_at=repository.utcnow() - timedelta(hours=13),
                expires_at=repository.utcnow() - timedelta(hours=1),
                revoked_at=None,
            )
        )

    response = await demo_client.get(
        "/api/v1/household", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_refresh_without_auth_is_401(demo_client) -> None:
    assert (await demo_client.post("/api/v1/auth/sessions/refresh")).status_code == 401
    assert (await demo_client.delete("/api/v1/auth/sessions")).status_code == 401
