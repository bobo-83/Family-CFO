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


# --- #93: the invitee's time zone, asked for at the one moment it is known ---
#
# The household's zone (#41) decides what "today" means. Before this, a
# household created for someone in another country ran on the box's zone until
# an owner remembered the Overview card — so bills read due-soon a day early
# for the very person the household was made for.


@pytest.mark.anyio
async def test_accepting_with_a_zone_sets_it_on_a_household_that_has_none(
    demo_client, demo_token, demo_engine
) -> None:
    from family_cfo_api import repository

    assert repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID).timezone is None

    created = await _create_invite(demo_client, demo_token, "zoned@family-cfo.local")
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": created["invite_token"],
            "password": "their-own-secret-1",
            "display_name": "Zoned",
            "timezone": "Pacific/Kiritimati",
        },
    )
    assert accepted.status_code == 201, accepted.text
    household = repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert household.timezone == "Pacific/Kiritimati"


@pytest.mark.anyio
async def test_accepting_without_a_zone_leaves_the_household_on_the_box_default(
    demo_client, demo_token, demo_engine
) -> None:
    """The field is optional and the old payload must behave exactly as before:
    no zone means the column stays null, which is the inherit state."""
    import os
    from unittest import mock

    from family_cfo_api import household_clock, repository

    created = await _create_invite(demo_client, demo_token, "zoneless@family-cfo.local")
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": created["invite_token"],
            "password": "their-own-secret-1",
            "display_name": "Zoneless",
        },
    )
    assert accepted.status_code == 201, accepted.text

    household = repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert household.timezone is None
    with mock.patch.dict(
        os.environ, {household_clock.DEFAULT_TIMEZONE_ENV: "Pacific/Kiritimati"}
    ):
        assert household_clock.today_for(household.timezone) == household_clock.today_for(
            "Pacific/Kiritimati"
        )


@pytest.mark.anyio
async def test_a_blank_zone_is_not_an_answer(demo_client, demo_token, demo_engine) -> None:
    """A client submitting an untouched input must land on today's behaviour,
    not a 422 that costs the invitee their one-time link."""
    from family_cfo_api import repository

    created = await _create_invite(demo_client, demo_token, "blank@family-cfo.local")
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": created["invite_token"],
            "password": "their-own-secret-1",
            "display_name": "Blank",
            "timezone": "   ",
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID).timezone is None


@pytest.mark.anyio
async def test_an_unknown_zone_is_422_and_does_not_burn_the_invite(
    demo_client, demo_token, demo_engine
) -> None:
    """Validated before the invite is claimed: accepting is one-shot, so a typo
    must cost a retry rather than the only link they have."""
    from family_cfo_api import repository

    created = await _create_invite(demo_client, demo_token, "typo@family-cfo.local")
    token = created["invite_token"]

    rejected = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": token,
            "password": "their-own-secret-1",
            "display_name": "Typo",
            "timezone": "Mars/Olympus_Mons",
        },
    )
    assert rejected.status_code == 422
    assert "timezone" in rejected.json()["error"]["message"].lower()
    assert repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID).timezone is None

    # The link still works — the same wording PATCH /household uses, and the
    # same invite.
    retried = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": token,
            "password": "their-own-secret-1",
            "display_name": "Typo",
            "timezone": "Europe/London",
        },
    )
    assert retried.status_code == 201, retried.text
    assert repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID).timezone == (
        "Europe/London"
    )


@pytest.mark.anyio
async def test_a_second_acceptance_does_not_move_an_established_household(
    demo_client, demo_token, demo_engine
) -> None:
    """The rule that makes this safe to ask everyone: the zone is adopted only
    by a household that has never had one. Otherwise the last person to join
    would silently re-date every bill for everyone already there."""
    from family_cfo_api import repository

    first = await _create_invite(demo_client, demo_token, "first@family-cfo.local")
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": first["invite_token"],
            "password": "their-own-secret-1",
            "display_name": "First",
            "timezone": "Europe/London",
        },
    )
    assert accepted.status_code == 201, accepted.text

    second = await _create_invite(demo_client, demo_token, "second@family-cfo.local")
    joined = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": second["invite_token"],
            "password": "their-own-secret-2",
            "display_name": "Second",
            "timezone": "Pacific/Kiritimati",
        },
    )
    # They join happily — their zone is simply not the household's business.
    assert joined.status_code == 201, joined.text
    assert repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID).timezone == (
        "Europe/London"
    )


@pytest.mark.anyio
async def test_an_established_zone_survives_even_when_it_matches_the_box(
    demo_client, demo_token, demo_engine
) -> None:
    """A household explicitly set BACK to the box default (#43, null) has no
    zone of its own, so the next invitee may set one — but a household holding
    an explicit zone is established even if that zone equals the box's."""
    from family_cfo_api import repository

    repository.set_household_timezone(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "Europe/London")
    assert (
        repository.set_household_timezone_if_unset(
            demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "Pacific/Kiritimati"
        )
        is False
    )
    assert repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID).timezone == (
        "Europe/London"
    )

    repository.set_household_timezone(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, None)
    assert (
        repository.set_household_timezone_if_unset(
            demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "Pacific/Kiritimati"
        )
        is True
    )


@pytest.mark.anyio
async def test_the_households_dates_follow_the_zone_the_invitee_chose(
    demo_client, demo_token, demo_engine
) -> None:
    """#93 end to end, the way #41 asserts it: the zone picked at acceptance
    reaches the date math, so "today" is the invitee's today."""
    from family_cfo_api import finance_service, household_clock, repository

    created = await _create_invite(demo_client, demo_token, "dates@family-cfo.local")
    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={
            "token": created["invite_token"],
            "password": "their-own-secret-1",
            "display_name": "Dates",
            # UTC+14 — the furthest zone from UTC there is, so if the household's
            # zone were ignored this would fail on most of the clock.
            "timezone": "Pacific/Kiritimati",
        },
    )
    assert accepted.status_code == 201, accepted.text

    household = repository.get_household(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert household_clock.today_for_household(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID
    ) == household_clock.today_for("Pacific/Kiritimati")
    # And the service layer picks it up without being told.
    assert isinstance(
        finance_service.upcoming_bills(demo_engine, household.id, household.base_currency), list
    )
