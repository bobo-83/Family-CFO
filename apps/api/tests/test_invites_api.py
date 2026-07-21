"""ADR 0056: copy-link invites — an admin shares a one-time link; the invitee
sets their own password and joins. Status is pending/accepted/expired/revoked."""

import pytest

from family_cfo_api import fixtures


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_invite(client, token: str, email: str, role: str = "adult") -> dict:
    response = await client.post(
        "/api/v1/household/invites",
        headers=_headers(token),
        json={"email": email, "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.anyio
async def test_invite_requires_members_manage(demo_client, demo_viewer_token) -> None:
    response = await demo_client.post(
        "/api/v1/household/invites",
        headers=_headers(demo_viewer_token),
        json={"email": "new@family-cfo.local"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_invite_create_list_and_accept_flow(demo_client, demo_token) -> None:
    created = await _create_invite(demo_client, demo_token, "kid@family-cfo.local")
    assert created["invite"]["status"] == "pending"
    assert created["invite"]["email"] == "kid@family-cfo.local"
    token = created["invite_token"]
    assert token  # the one-time secret

    # The list shows it pending and NEVER includes the token.
    listed = await demo_client.get("/api/v1/household/invites", headers=_headers(demo_token))
    assert listed.status_code == 200
    [invite] = [i for i in listed.json()["invites"] if i["email"] == "kid@family-cfo.local"]
    assert invite["status"] == "pending"
    assert "invite_token" not in invite

    # Public preview names the household and the invited email.
    preview = await demo_client.post("/api/v1/invites/preview", json={"token": token})
    assert preview.status_code == 200
    assert preview.json()["email"] == "kid@family-cfo.local"

    # Accept: the invitee sets their OWN password and is signed in.
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": token, "password": "their-own-secret-1", "display_name": "Kiddo"},
    )
    assert accepted.status_code == 201
    session = accepted.json()
    assert session["access_token"]
    assert session["household_id"] == fixtures.DEMO_HOUSEHOLD_ID

    # They can log in with those credentials…
    login = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": "kid@family-cfo.local", "password": "their-own-secret-1"},
    )
    assert login.status_code == 201

    # …the invite reads accepted, and the used link is dead.
    listed = await demo_client.get("/api/v1/household/invites", headers=_headers(demo_token))
    [invite] = [i for i in listed.json()["invites"] if i["email"] == "kid@family-cfo.local"]
    assert invite["status"] == "accepted"
    again = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": token, "password": "second-try-secret", "display_name": "X"},
    )
    assert again.status_code == 410


@pytest.mark.anyio
async def test_invite_for_existing_member_conflicts(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/household/invites",
        headers=_headers(demo_token),
        json={"email": fixtures.DEMO_VIEWER_EMAIL},
    )
    assert response.status_code == 409


@pytest.mark.anyio
async def test_revoked_invite_is_gone_and_revoke_is_undoable_shape(
    demo_client, demo_token
) -> None:
    created = await _create_invite(demo_client, demo_token, "aunt@family-cfo.local")
    invite_id = created["invite"]["id"]

    revoke = await demo_client.delete(
        f"/api/v1/household/invites/{invite_id}", headers=_headers(demo_token)
    )
    assert revoke.status_code == 204

    gone = await demo_client.post(
        "/api/v1/invites/preview", json={"token": created["invite_token"]}
    )
    assert gone.status_code == 410

    listed = await demo_client.get("/api/v1/household/invites", headers=_headers(demo_token))
    [invite] = [i for i in listed.json()["invites"] if i["id"] == invite_id]
    assert invite["status"] == "revoked"


@pytest.mark.anyio
async def test_regenerate_kills_old_link_and_mints_new(demo_client, demo_token) -> None:
    created = await _create_invite(demo_client, demo_token, "uncle@family-cfo.local")
    invite_id = created["invite"]["id"]
    old_token = created["invite_token"]

    regen = await demo_client.post(
        f"/api/v1/household/invites/{invite_id}/token", headers=_headers(demo_token)
    )
    assert regen.status_code == 201
    new_token = regen.json()["invite_token"]
    assert new_token != old_token

    assert (
        await demo_client.post("/api/v1/invites/preview", json={"token": old_token})
    ).status_code == 404
    assert (
        await demo_client.post("/api/v1/invites/preview", json={"token": new_token})
    ).status_code == 200


@pytest.mark.anyio
async def test_new_invite_for_same_email_revokes_the_old_link(demo_client, demo_token) -> None:
    first = await _create_invite(demo_client, demo_token, "twin@family-cfo.local")
    second = await _create_invite(demo_client, demo_token, "twin@family-cfo.local")

    assert (
        await demo_client.post("/api/v1/invites/preview", json={"token": first["invite_token"]})
    ).status_code == 410
    assert (
        await demo_client.post("/api/v1/invites/preview", json={"token": second["invite_token"]})
    ).status_code == 200


@pytest.mark.anyio
async def test_removed_member_rejoins_via_invite_reusing_their_account(
    demo_client, demo_token
) -> None:
    """The users row survives removal; accepting a fresh invite revives it with
    a NEW password and a fresh membership (ADR 0056)."""
    created = await _create_invite(demo_client, demo_token, "rejoin@family-cfo.local")
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": created["invite_token"], "password": "first-password-1", "display_name": "Rey"},
    )
    assert accepted.status_code == 201
    user_id = accepted.json()["user_id"]

    removed = await demo_client.delete(
        f"/api/v1/household/members/{user_id}", headers=_headers(demo_token)
    )
    assert removed.status_code == 204

    # While removed, a direct login is refused (no membership).
    refused = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": "rejoin@family-cfo.local", "password": "first-password-1"},
    )
    assert refused.status_code == 401

    invited_again = await _create_invite(demo_client, demo_token, "rejoin@family-cfo.local")
    rejoined = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": invited_again["invite_token"],
            "password": "second-password-2",
            "display_name": "Rey Again",
        },
    )
    assert rejoined.status_code == 201
    # Same human, same account id — history preserved.
    assert rejoined.json()["user_id"] == user_id

    # The old password is gone; the new one works.
    assert (
        await demo_client.post(
            "/api/v1/auth/sessions",
            json={"email": "rejoin@family-cfo.local", "password": "first-password-1"},
        )
    ).status_code == 401
    assert (
        await demo_client.post(
            "/api/v1/auth/sessions",
            json={"email": "rejoin@family-cfo.local", "password": "second-password-2"},
        )
    ).status_code == 201


@pytest.mark.anyio
async def test_accept_conflicts_when_email_gained_an_account(demo_client, demo_token) -> None:
    """An invite accepted after the email already joined (e.g. via a second
    invite) must not overwrite the live account's password."""
    first = await _create_invite(demo_client, demo_token, "race@family-cfo.local")
    token_one = first["invite_token"]
    second = await _create_invite(demo_client, demo_token, "race@family-cfo.local")
    # (second create revoked the first link; accept via the second)
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": second["invite_token"], "password": "winner-password", "display_name": "W"},
    )
    assert accepted.status_code == 201

    # The revoked first link is gone — and even a fresh invite for that email
    # now 409s at creation because the account exists with a membership.
    assert (
        await demo_client.post(
            "/api/v1/invites/accept",
            json={"token": token_one, "password": "loser-password-1", "display_name": "L"},
        )
    ).status_code == 410
    assert (
        await demo_client.post(
            "/api/v1/household/invites",
            headers=_headers(demo_token),
            json={"email": "race@family-cfo.local"},
        )
    ).status_code == 409


@pytest.mark.anyio
async def test_accept_works_while_household_bootstrap_stays_locked(demo_engine) -> None:
    """Single-tenant lockout (403 on POST /households) must not block joining
    the existing household via an invite."""
    import httpx
    from test_household_lockout import _app

    # Default settings: single-tenant lockout ON (unlike the shared demo_client).
    app = _app(demo_engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        bootstrap = await client.post(
            "/api/v1/households",
            json={
                "display_name": "Second Home",
                "base_currency": "USD",
                "owner_email": "other@family-cfo.local",
                "owner_password": "irrelevant-123",
                "owner_display_name": "Other",
            },
        )
        assert bootstrap.status_code == 403

        login = await client.post(
            "/api/v1/auth/sessions",
            json={"email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD},
        )
        owner_token = login.json()["access_token"]
        created = await _create_invite(client, owner_token, "lockout@family-cfo.local")
        accepted = await client.post(
            "/api/v1/invites/accept",
            json={
                "token": created["invite_token"],
                "password": "welcome-in-123",
                "display_name": "In",
            },
        )
        assert accepted.status_code == 201


@pytest.mark.anyio
async def test_unknown_token_is_404(demo_client) -> None:
    assert (
        await demo_client.post("/api/v1/invites/preview", json={"token": "nope"})
    ).status_code == 404
    assert (
        await demo_client.post(
            "/api/v1/invites/accept",
            json={"token": "nope", "password": "whatever-123", "display_name": "X"},
        )
    ).status_code == 404
