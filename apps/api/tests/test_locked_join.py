"""#103: joining a SEALED, LOCKED household — the refusal, and what it says.

A sealed household's DEK lives only in the session keyring (30-minute sliding
TTL). With no live session key, no new member can be created: minting their key
wrap requires the household key to be readable, and an account with no wrap
could never unlock anything. Refusing is correct.

What this file pins down is the part that was broken: the refusal has to name
the READER's next action, and the reader is a different person on each door.
"""

import pytest
from cryptography.fernet import Fernet

from family_cfo_api import fixtures, household_crypto, localization, repository
from family_cfo_api.api import invites, members
from family_cfo_api.config import get_settings


@pytest.fixture
def _master_key(monkeypatch):
    # Generated per test run — nothing key-shaped is ever committed.
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seal(client, engine, token: str) -> str:
    """Seal the demo household through the routes and return its id."""
    headers = _headers(token)
    household_id = repository.list_households(engine)[0]
    # A sealed row to read back: an empty list decrypts nothing and would let a
    # "household must be locked" assertion pass on an unlocked household.
    repository.upsert_household_memory(engine, household_id, "pet", "a golden retriever")
    member = repository.list_members(engine, household_id)[0]
    household_crypto.ensure_member_wrap(
        engine, household_id, member.user_id, fixtures.DEMO_USER_PASSWORD
    )
    await client.post("/api/v1/household/recovery-key", headers=headers)
    sealed = await client.post(
        "/api/v1/household/seal-mode", headers=headers, json={"mode": "sealed"}
    )
    assert sealed.status_code == 200, sealed.text
    return household_id


async def _pending_invite_token(client, token: str, email: str) -> str:
    created = await client.post(
        "/api/v1/household/invites",
        headers=_headers(token),
        json={"email": email, "role": "adult"},
    )
    assert created.status_code == 201, created.text
    return created.json()["invite_token"]


@pytest.mark.anyio
async def test_locked_household_still_refuses_both_doors(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """The refusal itself is the feature — #103 does not relax it."""
    await _seal(demo_client, demo_engine, demo_token)
    invite_token = await _pending_invite_token(demo_client, demo_token, "kid@family-cfo.local")

    # Box restart: the keyring was the only place the key lived.
    household_crypto.reset_cache_for_tests()
    locked = await demo_client.get("/api/v1/memories", headers=_headers(demo_token))
    assert locked.status_code == 423, "the household must actually be locked here"

    accept = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": invite_token, "password": "their-own-secret-1", "display_name": "Kiddo"},
    )
    assert accept.status_code == 423, accept.text

    add = await demo_client.post(
        "/api/v1/household/members",
        headers=_headers(demo_token),
        json={
            "email": "second@family-cfo.local",
            "password": "their-own-secret-1",
            "display_name": "Second",
            "role": "adult",
        },
    )
    assert add.status_code == 423, add.text


@pytest.mark.anyio
async def test_the_invite_link_survives_a_locked_refusal(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """The invitee is told to open the link again — so the link must still work.

    Refusing AFTER claiming the invite would burn the one-time token, and the
    advice would be a lie: the second visit would 410.
    """
    await _seal(demo_client, demo_engine, demo_token)
    invite_token = await _pending_invite_token(demo_client, demo_token, "kid@family-cfo.local")
    household_crypto.reset_cache_for_tests()

    refused = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": invite_token, "password": "their-own-secret-1", "display_name": "Kiddo"},
    )
    assert refused.status_code == 423, refused.text

    # An existing member signs in — the remedy the message names.
    login = await demo_client.post(
        "/api/v1/auth/sessions",
        json={"email": fixtures.DEMO_USER_EMAIL, "password": fixtures.DEMO_USER_PASSWORD},
    )
    assert login.status_code == 201, login.text

    accepted = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": invite_token, "password": "their-own-secret-1", "display_name": "Kiddo"},
    )
    assert accepted.status_code == 201, accepted.text


@pytest.mark.anyio
async def test_the_lock_message_names_each_reader_s_next_action(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """Properties, not prose: a copy tweak must not break this."""
    await _seal(demo_client, demo_engine, demo_token)
    invite_token = await _pending_invite_token(demo_client, demo_token, "kid@family-cfo.local")
    household_crypto.reset_cache_for_tests()

    accept = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": invite_token, "password": "their-own-secret-1", "display_name": "Kiddo"},
    )
    assert accept.status_code == 423
    invitee = accept.json()["error"]["message"]

    add = await demo_client.post(
        "/api/v1/household/members",
        headers=_headers(demo_token),
        json={
            "email": "second@family-cfo.local",
            "password": "their-own-secret-1",
            "display_name": "Second",
            "role": "adult",
        },
    )
    assert add.status_code == 423
    owner = add.json()["error"]["message"]

    # Both name signing in — the remedy — not just the machine's state.
    assert "sign in" in invitee.lower()
    assert "sign in" in owner.lower()

    # And they are NOT the same sentence: the invitee cannot sign in to a
    # household they have not joined, so they are told to ask someone who can.
    assert invitee != owner
    assert "ask" in invitee.lower()

    # Neither is the generic "sign in again to unlock it" aimed at a member.
    generic = str(household_crypto.HouseholdLockedError("hh"))
    assert invitee != generic
    assert owner != generic


@pytest.mark.anyio
async def test_a_blocked_join_carries_its_own_error_code(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """The web needs to tell this 423 from an ordinary one.

    An ordinary 423 means the session is stale and the web drops it in silence
    (#101). This one is about the action, and dropping the owner's session
    mid-form would throw them out of the very screen the message talks about.
    """
    await _seal(demo_client, demo_engine, demo_token)
    invite_token = await _pending_invite_token(demo_client, demo_token, "kid@family-cfo.local")
    household_crypto.reset_cache_for_tests()

    accept = await demo_client.post(
        "/api/v1/invites/accept",
        json={"token": invite_token, "password": "their-own-secret-1", "display_name": "Kiddo"},
    )
    add = await demo_client.post(
        "/api/v1/household/members",
        headers=_headers(demo_token),
        json={
            "email": "second@family-cfo.local",
            "password": "their-own-secret-1",
            "display_name": "Second",
            "role": "adult",
        },
    )
    ordinary = await demo_client.get("/api/v1/memories", headers=_headers(demo_token))

    assert accept.json()["error"]["code"] == household_crypto.LOCKED_NEW_MEMBER_CODE
    assert add.json()["error"]["code"] == household_crypto.LOCKED_NEW_MEMBER_CODE
    assert ordinary.json()["error"]["code"] == household_crypto.LOCKED_CODE
    assert household_crypto.LOCKED_NEW_MEMBER_CODE != household_crypto.LOCKED_CODE


def test_every_lock_message_is_translated() -> None:
    """A catalog key that does not match its constant fails silently — the
    reader just gets English. Caught here instead."""
    messages = [
        str(household_crypto.HouseholdLockedError("hh")),
        invites.INVITEE_LOCKED_MESSAGE,
        members.OWNER_LOCKED_MESSAGE,
    ]
    for message in messages:
        for locale in ("vi", "lt"):
            assert localization.translate(message, locale) != message, (message, locale)


@pytest.mark.anyio
async def test_preview_still_shows_the_invite_on_a_locked_household(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """The invitee must reach the FORM before they can be told anything useful.

    Nothing on the preview path touches encrypted content, and this keeps it
    that way — a 423 here would replace the join page with a dead end.
    """
    await _seal(demo_client, demo_engine, demo_token)
    invite_token = await _pending_invite_token(demo_client, demo_token, "kid@family-cfo.local")
    household_crypto.reset_cache_for_tests()

    preview = await demo_client.post("/api/v1/invites/preview", json={"token": invite_token})
    assert preview.status_code == 200, preview.text
    assert preview.json()["email"] == "kid@family-cfo.local"
