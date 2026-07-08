import json

import pytest


@pytest.mark.anyio
async def test_create_pairing_session_requires_authentication(demo_client) -> None:
    response = await demo_client.post("/api/v1/pairing/sessions")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_viewer_cannot_create_pairing_session(demo_client, demo_viewer_token) -> None:
    response = await demo_client.post(
        "/api/v1/pairing/sessions",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
    )

    assert response.status_code == 403


@pytest.mark.anyio
async def test_owner_can_create_and_confirm_pairing_session(demo_client, demo_token) -> None:
    created = await demo_client.post(
        "/api/v1/pairing/sessions",
        headers={"Authorization": f"Bearer {demo_token}"},
    )

    assert created.status_code == 201
    session_body = created.json()
    qr_payload = json.loads(session_body["qr_payload"])
    assert qr_payload["pairing_session_id"] == session_body["id"]
    assert qr_payload["household_name"] == "The Demo Family"
    assert "access_token" not in session_body["qr_payload"]

    confirmed = await demo_client.post(
        "/api/v1/pairing/confirm",
        json={
            "pairing_session_id": session_body["id"],
            "device_name": "Alex's iPhone",
            "device_public_key": "public-key",
        },
    )

    assert confirmed.status_code == 200
    credential = confirmed.json()
    assert credential["device_id"]
    assert credential["access_token"]

    devices = await demo_client.get(
        "/api/v1/pairing/devices",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert devices.status_code == 200
    assert devices.json()["devices"][0]["name"] == "Alex's iPhone"


@pytest.mark.anyio
async def test_pairing_confirmation_is_single_use(demo_client, demo_token) -> None:
    created = await demo_client.post(
        "/api/v1/pairing/sessions",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    payload = {
        "pairing_session_id": created.json()["id"],
        "device_name": "Alex's iPhone",
        "device_public_key": "public-key",
    }

    first = await demo_client.post("/api/v1/pairing/confirm", json=payload)
    second = await demo_client.post("/api/v1/pairing/confirm", json=payload)

    assert first.status_code == 200
    assert second.status_code == 400


@pytest.mark.anyio
async def test_revoke_paired_device_invalidates_device_token(demo_client, demo_token) -> None:
    created = await demo_client.post(
        "/api/v1/pairing/sessions",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    confirmed = await demo_client.post(
        "/api/v1/pairing/confirm",
        json={
            "pairing_session_id": created.json()["id"],
            "device_name": "Alex's iPhone",
            "device_public_key": "public-key",
        },
    )
    credential = confirmed.json()

    authorized_before_revoke = await demo_client.get(
        "/api/v1/household",
        headers={"Authorization": f"Bearer {credential['access_token']}"},
    )
    revoked = await demo_client.delete(
        f"/api/v1/pairing/devices/{credential['device_id']}",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    authorized_after_revoke = await demo_client.get(
        "/api/v1/household",
        headers={"Authorization": f"Bearer {credential['access_token']}"},
    )

    assert authorized_before_revoke.status_code == 200
    assert revoked.status_code == 204
    assert authorized_after_revoke.status_code == 401


@pytest.mark.anyio
async def test_viewer_cannot_revoke_paired_device(
    demo_client, demo_token, demo_viewer_token
) -> None:
    created = await demo_client.post(
        "/api/v1/pairing/sessions",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    confirmed = await demo_client.post(
        "/api/v1/pairing/confirm",
        json={
            "pairing_session_id": created.json()["id"],
            "device_name": "Alex's iPhone",
            "device_public_key": "public-key",
        },
    )

    response = await demo_client.delete(
        f"/api/v1/pairing/devices/{confirmed.json()['device_id']}",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
    )

    assert response.status_code == 403
