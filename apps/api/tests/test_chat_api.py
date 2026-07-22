from sqlalchemy import select

import pytest

from family_cfo_api import models


@pytest.mark.anyio
async def test_chat_requires_authentication(demo_client) -> None:
    response = await demo_client.post("/api/v1/chat/messages", json={"message": "Where are we?"})

    assert response.status_code == 401


@pytest.mark.anyio
async def test_chat_returns_calculation_referenced_recommendation(
    demo_client, demo_engine, demo_token
) -> None:
    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "How are we doing?"},
    )

    assert response.status_code == 200
    body = response.json()
    recommendation = body["recommendation"]
    assert body["conversation_id"]
    assert recommendation["id"]
    assert len(recommendation["calculation_refs"]) == 2
    assert all(
        ref.startswith("financial_calculations:") for ref in recommendation["calculation_refs"]
    )
    assert recommendation["impacts"][0]["area"] == "net_worth"

    with demo_engine.connect() as conn:
        stored = (
            conn.execute(
                select(models.recommendations).where(
                    models.recommendations.c.id == recommendation["id"]
                )
            )
            .mappings()
            .first()
        )
        scenario_count = len(conn.execute(select(models.scenarios)).all())

    assert stored is not None
    assert stored["scenario_id"] is None
    assert scenario_count == 0


@pytest.mark.anyio
async def test_chat_appends_to_an_existing_conversation(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    # M10: a first call creates a real conversation; a follow-up with that id appends to it.
    first = await demo_client.post(
        "/api/v1/chat/messages", headers=headers, json={"message": "Start"}
    )
    conversation_id = first.json()["conversation_id"]

    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"conversation_id": conversation_id, "message": "Continue"},
    )

    assert response.status_code == 200
    assert response.json()["conversation_id"] == conversation_id


@pytest.mark.anyio
async def test_chat_with_unknown_conversation_id_starts_a_new_thread(
    demo_client, demo_token
) -> None:
    # An unknown/other-household id cannot be appended to; a new conversation is started instead.
    unknown_id = "77777777-7777-7777-7777-777777777777"
    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"conversation_id": unknown_id, "message": "Continue"},
    )

    assert response.status_code == 200
    assert response.json()["conversation_id"] != unknown_id


@pytest.mark.anyio
async def test_chat_stream_requires_authentication(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/chat/messages/stream", json={"message": "Where are we?"}
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_chat_stream_emits_progress_then_one_validated_answer(
    demo_client, demo_token
) -> None:
    # ADR 0061: the stream narrates progress and then delivers the SAME
    # response shape the plain endpoint returns — exactly once, and only
    # after grounding. Keepalive comments (": ping") are not events.
    import json as jsonlib

    events = []
    async with demo_client.stream(
        "POST",
        "/api/v1/chat/messages/stream",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "How are we doing?"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(jsonlib.loads(line[len("data: "):]))

    assert [e["type"] for e in events[:-1]] == ["progress"] * (len(events) - 1)
    assert events[0]["stage"] == "thinking"
    answers = [e for e in events if e["type"] == "answer"]
    assert len(answers) == 1
    assert events[-1]["type"] == "answer"
    body = answers[0]["response"]
    assert body["conversation_id"]
    assert body["recommendation"]["answer"]
    assert body["recommendation"]["calculation_refs"]


@pytest.mark.anyio
async def test_chat_stream_persists_the_turn_like_the_plain_endpoint(
    demo_client, demo_token
) -> None:
    import json as jsonlib

    headers = {"Authorization": f"Bearer {demo_token}"}
    answer = None
    async with demo_client.stream(
        "POST",
        "/api/v1/chat/messages/stream",
        headers=headers,
        json={"message": "Snapshot please"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                event = jsonlib.loads(line[len("data: "):])
                if event["type"] == "answer":
                    answer = event["response"]

    assert answer is not None
    detail = await demo_client.get(
        f"/api/v1/conversations/{answer['conversation_id']}", headers=headers
    )
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert messages[0]["content"] == "Snapshot please"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == answer["recommendation"]["answer"]
