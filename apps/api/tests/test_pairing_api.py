import pytest

from family_cfo_api import fixtures


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/pairing/sessions", "/pairing/confirm"])
@pytest.mark.parametrize("authenticated", [False, True])
async def test_qr_bootstrap_routes_are_retired(demo_client, demo_token, path, authenticated):
    headers = {"Authorization": f"Bearer {demo_token}"} if authenticated else {}
    response = await demo_client.post(
        f"/api/v1{path}", headers=headers,
        json={"pairing_session_id": "outstanding-pre-upgrade-code",
              "device_name": "Old app", "device_public_key": "k"},
    )
    assert response.status_code == 404


@pytest.mark.anyio
async def test_viewer_cannot_revoke_an_account_device(demo_client, demo_viewer_token):
    login = await demo_client.post(
        "/api/v1/pairing/login", json={
            "email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD,
            "device_name": "Account device", "device_public_key": "k",
        },
    )
    assert login.status_code == 201
    refused = await demo_client.delete(
        f"/api/v1/pairing/devices/{login.json()['device_id']}",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
    )
    assert refused.status_code == 403


@pytest.mark.anyio
async def test_pairing_login_creates_a_paired_device(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/pairing/login",
        json={
            "email": fixtures.DEMO_USER_EMAIL,
            "password": fixtures.DEMO_USER_PASSWORD,
            "device_name": "Bo's iPhone (login)",
            "device_public_key": "login-public-key",
        },
    )
    assert response.status_code == 201, response.text
    credential = response.json()
    assert credential["access_token"]
    # ADR 0056: the login path has no QR payload — the credential carries the
    # household so the phone can build its ServerConfig.
    assert credential["household_id"] == fixtures.DEMO_HOUSEHOLD_ID
    assert credential["household_name"]

    # The device token authenticates like any paired device…
    me = await demo_client.get(
        "/api/v1/household/members",
        headers={"Authorization": f"Bearer {credential['access_token']}"},
    )
    assert me.status_code == 200

    # …and the phone shows on the Devices page.
    devices = await demo_client.get(
        "/api/v1/pairing/devices", headers={"Authorization": f"Bearer {demo_token}"}
    )
    names = [d["name"] for d in devices.json()["devices"]]
    assert "Bo's iPhone (login)" in names


@pytest.mark.anyio
async def test_pairing_login_rejects_bad_password(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/pairing/login",
        json={
            "email": fixtures.DEMO_USER_EMAIL,
            "password": "wrong-password",
            "device_name": "Mystery phone",
            "device_public_key": "k",
        },
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_revoking_a_login_device_kills_its_session(demo_client, demo_token) -> None:
    credential = (
        await demo_client.post(
            "/api/v1/pairing/login",
            json={
                "email": fixtures.DEMO_USER_EMAIL,
                "password": fixtures.DEMO_USER_PASSWORD,
                "device_name": "Revocable phone",
                "device_public_key": "k",
            },
        )
    ).json()

    revoked = await demo_client.delete(
        f"/api/v1/pairing/devices/{credential['device_id']}",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert revoked.status_code == 204

    refused = await demo_client.get(
        "/api/v1/household/members",
        headers={"Authorization": f"Bearer {credential['access_token']}"},
    )
    assert refused.status_code == 401


@pytest.mark.anyio
async def test_cannot_revoke_the_calling_device(demo_client, demo_token) -> None:
    """The footgun guard: a device may not revoke itself (that kills its own
    session mid-request). The session-info endpoint identifies the current
    device so a client can mark it and hide its revoke action."""
    confirmed = await demo_client.post(
        "/api/v1/pairing/login",
        json={
            "email": fixtures.DEMO_USER_EMAIL,
            "password": fixtures.DEMO_USER_PASSWORD,
            "device_name": "Test phone",
            "device_public_key": "public-key",
        },
    )
    assert confirmed.status_code == 201
    credential = confirmed.json()
    device_headers = {"Authorization": f"Bearer {credential['access_token']}"}

    session = await demo_client.get("/api/v1/auth/session", headers=device_headers)
    assert session.status_code == 200
    assert session.json()["device_id"] == credential["device_id"]

    refused = await demo_client.delete(
        f"/api/v1/pairing/devices/{credential['device_id']}", headers=device_headers
    )
    assert refused.status_code == 409
    assert "using" in refused.json()["error"]["message"].lower()

    # Still valid — the self-revoke never happened.
    still_ok = await demo_client.get("/api/v1/household", headers=device_headers)
    assert still_ok.status_code == 200


@pytest.mark.anyio
async def test_device_last_seen_is_stamped_on_use(demo_client, demo_token) -> None:
    confirmed = await demo_client.post(
        "/api/v1/pairing/login",
        json={
            "email": fixtures.DEMO_USER_EMAIL,
            "password": fixtures.DEMO_USER_PASSWORD,
            "device_name": "Test phone",
            "device_public_key": "public-key",
        },
    )
    assert confirmed.status_code == 201
    credential = confirmed.json()
    device_headers = {"Authorization": f"Bearer {credential['access_token']}"}

    # A newly paired device has never been "used" until it makes a call.
    devices = (
        await demo_client.get("/api/v1/pairing/devices", headers={"Authorization": f"Bearer {demo_token}"})
    ).json()["devices"]
    fresh = next(d for d in devices if d["id"] == credential["device_id"])
    assert fresh["last_seen_at"] is None

    await demo_client.get("/api/v1/household", headers=device_headers)

    devices = (
        await demo_client.get("/api/v1/pairing/devices", headers={"Authorization": f"Bearer {demo_token}"})
    ).json()["devices"]
    seen = next(d for d in devices if d["id"] == credential["device_id"])
    assert seen["last_seen_at"] is not None
