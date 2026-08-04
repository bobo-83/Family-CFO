"""#180: operator-driven multi-household onboarding.

The operator (a system admin) mints a household shell + a one-time owner
invite; the family's first owner joins with their own password (which also
mints their member key). Public signup stays locked the whole time.
"""

import pytest


@pytest.mark.anyio
async def test_hosted_household_end_to_end(demo_client, demo_token, demo_engine) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}

    created = await demo_client.post(
        "/api/v1/households/hosted",
        headers=headers,
        json={
            "display_name": "Cedar family",
            "base_currency": "usd",
            "owner_email": "cedar-owner@example.test",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["household"]["name"] == "Cedar family"
    assert body["household"]["base_currency"] == "USD"
    assert body["household"]["member_count"] == 0
    assert body["household"]["pending_owner_invite"] is True
    token = body["invite_token"]

    # The operator's list shows both households.
    listing = await demo_client.get("/api/v1/households/hosted", headers=headers)
    assert listing.status_code == 200
    names = [h["name"] for h in listing.json()["households"]]
    assert "Cedar family" in names and len(names) >= 2

    # The family's first owner joins through the standard invite flow, setting
    # their own password — never seen by the operator.
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": token,
            "password": "cedar-secret-pw",
            "display_name": "Cedar Owner",
        },
    )
    assert accepted.status_code in (200, 201), accepted.text
    session = accepted.json()
    new_hh = session["household_id"]
    assert new_hh == body["household"]["id"]
    assert session["role"] == "owner"

    # Isolation: the new owner sees an empty household, not the demo data.
    owner_headers = {"Authorization": f"Bearer {session['access_token']}"}
    accounts = await demo_client.get("/api/v1/accounts", headers=owner_headers)
    assert accounts.status_code == 200
    assert accounts.json()["accounts"] == []

    # They are NOT a system admin (the roster wasn't empty).
    info = await demo_client.get("/api/v1/auth/session", headers=owner_headers)
    assert info.json()["is_system_admin"] is False

    # The operator's list now shows 1 member, no pending invite.
    listing = await demo_client.get("/api/v1/households/hosted", headers=headers)
    cedar = next(h for h in listing.json()["households"] if h["id"] == new_hh)
    assert cedar["member_count"] == 1
    assert cedar["pending_owner_invite"] is False


@pytest.mark.anyio
async def test_hosted_creation_requires_system_admin(
    demo_client, demo_viewer_token
) -> None:
    refused = await demo_client.post(
        "/api/v1/households/hosted",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
        json={
            "display_name": "Nope",
            "base_currency": "USD",
            "owner_email": "nope@example.test",
        },
    )
    assert refused.status_code == 403


@pytest.mark.anyio
async def test_public_signup_stays_locked(demo_file_client) -> None:
    """Hosting does not open the public door: with the flag off (the
    single-box default — demo_file_settings leaves it) and a household
    present, the bootstrap endpoint still refuses."""
    refused = await demo_file_client.post(
        "/api/v1/households",
        json={
            "display_name": "Walk-in family",
            "base_currency": "USD",
            "owner_email": "walkin@example.test",
            "owner_password": "walkin-pw-123",
            "owner_display_name": "Walk In",
        },
    )
    assert refused.status_code == 403


@pytest.mark.anyio
async def test_existing_email_is_refused(demo_client, demo_token) -> None:
    from family_cfo_api import fixtures

    refused = await demo_client.post(
        "/api/v1/households/hosted",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "display_name": "Dup family",
            "base_currency": "USD",
            "owner_email": fixtures.DEMO_USER_EMAIL,
        },
    )
    assert refused.status_code == 409
