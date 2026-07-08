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
