"""#120: a locked household must fail the chat turn with a status code.

The advisor was the one screen in the app that could not recover from a locked
household. `HouseholdUnlockMiddleware` on the paired device triggers on a 423
and nothing else — it fetches the device's wrap, unwraps it locally, posts the
key back and replays the request, invisibly. The streamed endpoint committed
200 the moment the stream opened, so the failure surfaced inside the body, no
423 was ever emitted, the middleware never fired, and the reader got "The
advisor hit an unexpected error." while every other screen self-healed.

So the guarantee under test is not the wording. It is that the refusal carries
a 423 the device can act on, and that it arrives BEFORE the model runs.
"""

import json as jsonlib

import pytest
from cryptography.fernet import Fernet

from family_cfo_api import household_crypto, repository
from family_cfo_api.config import get_settings


@pytest.fixture
def _master_key(monkeypatch):
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()


def _seal_and_lock(engine, household_id: str) -> None:
    """Seal the household, then drop the session key — the state a device
    arrives in when its key session has expired."""
    member = repository.list_members(engine, household_id)[0]
    household_crypto.ensure_member_wrap(engine, household_id, member.user_id, "demo-password")
    household_crypto.generate_recovery_key(engine, household_id)
    assert household_crypto.seal_household(engine, household_id) is None
    household_crypto.reset_cache_for_tests()
    assert household_crypto.dek_available(engine, household_id) is False


@pytest.mark.anyio
async def test_locked_household_refuses_the_stream_with_423_not_a_stream(
    _master_key, demo_client, demo_token, demo_engine
) -> None:
    """THE regression. A 423 with the code is what makes the device unlock and
    replay; a 200 carrying an error event is what left the user stuck."""
    hh = repository.list_households(demo_engine)[0]
    _seal_and_lock(demo_engine, hh)

    response = await demo_client.post(
        "/api/v1/chat/messages/stream",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "If I lose my job can I dig into my retirement account?"},
    )

    assert response.status_code == 423, response.text
    assert not response.headers["content-type"].startswith("text/event-stream")
    body = response.json()["error"]
    assert body["code"] == household_crypto.LOCKED_CODE


@pytest.mark.anyio
async def test_locked_household_refuses_before_the_model_is_called(
    _master_key, demo_client, demo_token, demo_engine, monkeypatch
) -> None:
    """The turn is doomed either way; it must not spend a model call finding
    out. The original failure burned about a minute of GPU on an answer that
    could never be stored."""
    from family_cfo_api.api import chat as chat_module

    hh = repository.list_households(demo_engine)[0]
    _seal_and_lock(demo_engine, hh)

    called = []
    monkeypatch.setattr(
        chat_module,
        "_chat_turn",
        lambda *a, **k: called.append(1),
    )

    response = await demo_client.post(
        "/api/v1/chat/messages/stream",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "Where are we?"},
    )

    assert response.status_code == 423
    assert called == []


@pytest.mark.anyio
async def test_the_plain_endpoint_refuses_early_too(
    _master_key, demo_client, demo_token, demo_engine, monkeypatch
) -> None:
    """This path always surfaced the 423 correctly — but only after the model
    had run, because _chat_turn raised at the save step."""
    from family_cfo_api.api import chat as chat_module

    hh = repository.list_households(demo_engine)[0]
    _seal_and_lock(demo_engine, hh)

    called = []
    monkeypatch.setattr(chat_module, "_chat_turn", lambda *a, **k: called.append(1))

    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "Where are we?"},
    )

    assert response.status_code == 423
    assert response.json()["error"]["code"] == household_crypto.LOCKED_CODE
    assert called == []


@pytest.mark.anyio
async def test_a_mid_turn_lock_reports_a_code_rather_than_unexpected(
    _master_key, demo_client, demo_token, demo_engine, monkeypatch
) -> None:
    """Locking DURING a turn is past the point where a status code is possible.
    The stream must still say what happened, with a code to switch on — not the
    generic 'unexpected error' that started this."""
    from family_cfo_api.api import chat as chat_module

    hh = repository.list_households(demo_engine)[0]

    def lock_mid_turn(*_args, **_kwargs):
        raise household_crypto.HouseholdLockedError(hh)

    monkeypatch.setattr(chat_module, "_chat_turn", lock_mid_turn)

    events = []
    async with demo_client.stream(
        "POST",
        "/api/v1/chat/messages/stream",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "Where are we?"},
    ) as response:
        assert response.status_code == 200  # the stream had already opened
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(jsonlib.loads(line[len("data: "):]))

    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == household_crypto.LOCKED_CODE
    assert "unexpected" not in errors[0]["message"].lower()


@pytest.mark.anyio
async def test_an_unlocked_household_streams_normally(
    _master_key, demo_client, demo_token
) -> None:
    """The guard must not stand between a working household and its advisor."""
    events = []
    async with demo_client.stream(
        "POST",
        "/api/v1/chat/messages/stream",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "How are we doing?"},
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(jsonlib.loads(line[len("data: "):]))

    assert [e["type"] for e in events if e["type"] == "answer"] == ["answer"]
