import json

import pytest


@pytest.mark.anyio
async def test_create_pairing_session_requires_authentication(demo_client) -> None:
    response = await demo_client.post("/api/v1/pairing/sessions")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_any_member_can_pair_their_own_device(demo_client, demo_viewer_token) -> None:
    """ADR 0034: pairing YOUR OWN phone needs only membership — a viewer's device
    still acts as the viewer. Pairing for someone else needs devices.manage."""
    response = await demo_client.post(
        "/api/v1/pairing/sessions",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
    )

    assert response.status_code == 201


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
    # M83: the device acts as the pairing session's creator (the demo owner),
    # and the credential says so for the mobile app's role-aware shell.
    assert credential["role"] == "owner"

    devices = await demo_client.get(
        "/api/v1/pairing/devices",
        headers={"Authorization": f"Bearer {demo_token}"},
    )
    assert devices.status_code == 200
    device = devices.json()["devices"][0]
    assert device["name"] == "Alex's iPhone"
    # The device is paired AS the owner (M83), so the list attributes it to a
    # member — the web dashboard groups each member's devices under their row.
    assert device["user_id"] is not None


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


# --- M83a: certificate fingerprint in the QR payload ---


def test_certificate_fingerprint_hashes_the_der_bytes(tmp_path) -> None:
    import base64
    import hashlib

    from family_cfo_api.api.pairing import certificate_fingerprint

    der = b"not-a-real-cert-but-der-shaped-bytes"
    pem = (
        "-----BEGIN CERTIFICATE-----\n"
        + base64.encodebytes(der).decode()
        + "-----END CERTIFICATE-----\n"
    )
    cert = tmp_path / "tls.crt"
    cert.write_text(pem)

    assert certificate_fingerprint(str(cert)) == hashlib.sha256(der).hexdigest()
    assert certificate_fingerprint(str(tmp_path / "missing.crt")) is None
    assert certificate_fingerprint("") is None


@pytest.mark.anyio
async def test_qr_payload_carries_the_fingerprint_when_cert_configured(
    demo_client, demo_token, tmp_path, monkeypatch
) -> None:
    import base64
    import hashlib

    from family_cfo_api import config

    der = b"qr-payload-cert"
    cert = tmp_path / "tls.crt"
    cert.write_text(
        "-----BEGIN CERTIFICATE-----\n"
        + base64.encodebytes(der).decode()
        + "-----END CERTIFICATE-----\n"
    )
    monkeypatch.setattr(
        config, "get_settings", lambda: config.Settings(tls_cert_path=str(cert))
    )

    created = await demo_client.post(
        "/api/v1/pairing/sessions", headers={"Authorization": f"Bearer {demo_token}"}
    )

    assert created.status_code == 201
    payload = json.loads(created.json()["qr_payload"])
    assert payload["certificate_sha256"] == hashlib.sha256(der).hexdigest()


@pytest.mark.anyio
async def test_qr_payload_fingerprint_is_null_without_a_cert(demo_client, demo_token) -> None:
    created = await demo_client.post(
        "/api/v1/pairing/sessions", headers={"Authorization": f"Bearer {demo_token}"}
    )

    assert created.status_code == 201
    assert json.loads(created.json()["qr_payload"])["certificate_sha256"] is None


def test_pairing_session_id_column_fits_the_csprng_token() -> None:
    """Regression: the id column must hold the token_urlsafe secret.

    SQLite ignores VARCHAR length, so the width mismatch (String(36) vs the
    ~43-char token) only failed on PostgreSQL. This asserts the invariant
    directly so it can never regress on either backend.
    """
    from family_cfo_api import models, security

    column_length = models.pairing_sessions.c.id.type.length
    # Sample several tokens; token_urlsafe length is deterministic per byte
    # count but assert with margin against the actual generator.
    longest = max(len(security.generate_pairing_secret()) for _ in range(50))
    assert longest <= column_length, (
        f"pairing token ({longest} chars) does not fit id column "
        f"({column_length} chars)"
    )


@pytest.mark.anyio
async def test_qr_base_url_uses_forwarded_proto_and_host(demo_client, demo_token) -> None:
    """The QR must point at the externally reachable https host:port, not the
    internal proxied request (regression: it encoded http without the port)."""
    created = await demo_client.post(
        "/api/v1/pairing/sessions",
        headers={
            "Authorization": f"Bearer {demo_token}",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "192.168.1.10:8443",
        },
    )

    assert created.status_code == 201
    payload = json.loads(created.json()["qr_payload"])
    assert payload["api_base_url"] == "https://192.168.1.10:8443/api/v1"


@pytest.mark.anyio
async def test_owner_can_pair_a_device_for_another_member(demo_client, demo_token) -> None:
    """An owner mints a pairing code FOR a member, so a regular member never signs
    into the dashboard to pair their phone. The paired device acts as that member."""
    owner = {"Authorization": f"Bearer {demo_token}"}
    added = await demo_client.post(
        "/api/v1/household/members",
        headers=owner,
        json={
            "email": "wife@example.com",
            "password": "correcthorsebattery",
            "display_name": "Wife",
            "role": "adult",
        },
    )
    assert added.status_code == 201
    member_id = added.json()["user_id"]

    created = await demo_client.post(
        "/api/v1/pairing/sessions", headers=owner, json={"user_id": member_id}
    )
    assert created.status_code == 201
    confirmed = await demo_client.post(
        "/api/v1/pairing/confirm",
        json={
            "pairing_session_id": created.json()["id"],
            "device_name": "Wife's iPhone",
            "device_public_key": "public-key",
        },
    )
    assert confirmed.status_code == 200
    # Acts as the MEMBER (adult), NOT the owner who generated the code.
    assert confirmed.json()["role"] == "adult"


@pytest.mark.anyio
async def test_pairing_for_a_non_member_is_rejected(demo_client, demo_token) -> None:
    resp = await demo_client.post(
        "/api/v1/pairing/sessions",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"user_id": "not-a-member"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_non_owner_cannot_pair_for_another_member(demo_client, demo_token) -> None:
    """A regular member can pair their OWN phone, but minting a code for someone
    else would be privilege escalation — forbidden."""
    owner = {"Authorization": f"Bearer {demo_token}"}
    members = await demo_client.get("/api/v1/household/members", headers=owner)
    owner_id = next(m["user_id"] for m in members.json()["members"] if m["role"] == "owner")
    await demo_client.post(
        "/api/v1/household/members",
        headers=owner,
        json={
            "email": "adult2@example.com",
            "password": "correcthorsebattery",
            "display_name": "Adult",
            "role": "adult",
        },
    )
    login = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": "adult2@example.com", "password": "correcthorsebattery"},
    )
    adult_token = login.json()["access_token"]
    resp = await demo_client.post(
        "/api/v1/pairing/sessions",
        headers={"Authorization": f"Bearer {adult_token}"},
        json={"user_id": owner_id},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_generating_a_new_qr_invalidates_the_previous(demo_client, demo_token) -> None:
    """One valid QR per user: minting a new code makes any earlier one unusable,
    so a previously shown or leaked code can never pair."""
    owner = {"Authorization": f"Bearer {demo_token}"}
    first = await demo_client.post("/api/v1/pairing/sessions", headers=owner)
    second = await demo_client.post("/api/v1/pairing/sessions", headers=owner)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] != second.json()["id"]

    # The FIRST (now superseded) code can no longer pair.
    stale = await demo_client.post(
        "/api/v1/pairing/confirm",
        json={
            "pairing_session_id": first.json()["id"],
            "device_name": "Old",
            "device_public_key": "k",
        },
    )
    assert stale.status_code == 400

    # The LATEST code still works.
    ok = await demo_client.post(
        "/api/v1/pairing/confirm",
        json={
            "pairing_session_id": second.json()["id"],
            "device_name": "New",
            "device_public_key": "k",
        },
    )
    assert ok.status_code == 200
