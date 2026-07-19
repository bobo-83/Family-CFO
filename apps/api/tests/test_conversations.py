import pytest

from family_cfo_api import fixtures, repository


def test_conversation_repository_lifecycle(demo_engine) -> None:
    conversation = repository.create_conversation(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, fixtures.DEMO_USER_ID, "First chat"
    )
    recommendation_id = repository.create_recommendation(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        scenario_id=None,
        answer="An answer",
        assumptions=[],
        impacts=[],
        tradeoffs=[],
        alternatives=[],
        confidence=0.8,
        calculation_refs=[],
        warnings=[],
        explanation_source="deterministic_stub",
    )
    repository.append_conversation_turn(
        demo_engine, conversation.id, "Hello", "An answer", recommendation_id
    )
    repository.append_conversation_turn(
        demo_engine, conversation.id, "Again", "Another answer", recommendation_id
    )

    messages = repository.list_conversation_messages(demo_engine, conversation.id)
    assert [m.sequence for m in messages] == [1, 2, 3, 4]
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[1].recommendation_id == recommendation_id

    fetched = repository.get_conversation(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, conversation.id, fixtures.DEMO_USER_ID)
    assert fetched.message_count == 4

    assert repository.delete_conversation(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, conversation.id, fixtures.DEMO_USER_ID)
    assert (
        repository.get_conversation(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, conversation.id, fixtures.DEMO_USER_ID)
        is None
    )
    assert repository.list_conversation_messages(demo_engine, conversation.id) == []


def test_conversation_household_scoping(demo_engine) -> None:
    other = repository.create_household_with_owner(
        demo_engine,
        display_name="Other",
        base_currency="USD",
        owner_email="other@example.com",
        owner_password_hash="x",
        owner_display_name="Other Owner",
    )
    conversation = repository.create_conversation(
        demo_engine, other.household_id, other.user_id, "Theirs"
    )
    assert (
        repository.get_conversation(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, conversation.id, fixtures.DEMO_USER_ID)
        is None
    )
    assert (
        repository.delete_conversation(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, conversation.id, fixtures.DEMO_USER_ID)
        is False
    )


def test_conversation_private_to_creating_user(demo_engine) -> None:
    """ADR 0038: a conversation is invisible to other members of the same household."""
    from family_cfo_api import security

    other = repository.create_member(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        email="spouse@example.com",
        password_hash=security.hash_password("password-123"),
        display_name="Spouse",
        role="adult",
    )
    conversation = repository.create_conversation(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, fixtures.DEMO_USER_ID, "Mine"
    )

    # Same household, different member — can't read, list, or delete it.
    assert (
        repository.get_conversation(
            demo_engine, fixtures.DEMO_HOUSEHOLD_ID, conversation.id, other.user_id
        )
        is None
    )
    assert (
        repository.delete_conversation(
            demo_engine, fixtures.DEMO_HOUSEHOLD_ID, conversation.id, other.user_id
        )
        is False
    )
    other_list = repository.list_conversations(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, other.user_id
    )
    assert all(c.id != conversation.id for c in other_list)

    # The creator still sees their own.
    assert (
        repository.get_conversation(
            demo_engine, fixtures.DEMO_HOUSEHOLD_ID, conversation.id, fixtures.DEMO_USER_ID
        )
        is not None
    )


@pytest.mark.anyio
async def test_chat_creates_then_appends_conversation(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    first = await demo_client.post(
        "/api/v1/chat/messages", headers=headers, json={"message": "How are we doing?"}
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    second = await demo_client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"message": "And our savings?", "conversation_id": conversation_id},
    )
    assert second.json()["conversation_id"] == conversation_id

    listing = await demo_client.get("/api/v1/conversations", headers=headers)
    assert listing.status_code == 200
    conversations = listing.json()["conversations"]
    assert len(conversations) == 1
    assert conversations[0]["message_count"] == 4
    assert conversations[0]["title"].startswith("How are we doing")

    detail = await demo_client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    # The assistant turn links to a real recommendation (grounding preserved).
    assert messages[1]["recommendation_id"] is not None


@pytest.mark.anyio
async def test_conversation_delete_and_cross_household_404(demo_client, demo_token) -> None:
    headers = {"Authorization": f"Bearer {demo_token}"}
    created = await demo_client.post(
        "/api/v1/chat/messages", headers=headers, json={"message": "Snapshot please"}
    )
    conversation_id = created.json()["conversation_id"]

    deleted = await demo_client.delete(f"/api/v1/conversations/{conversation_id}", headers=headers)
    assert deleted.status_code == 204

    missing = await demo_client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_viewer_cannot_delete_conversation(
    demo_client, demo_token, demo_viewer_token
) -> None:
    created = await demo_client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "Snapshot"},
    )
    conversation_id = created.json()["conversation_id"]

    response = await demo_client.delete(
        f"/api/v1/conversations/{conversation_id}",
        headers={"Authorization": f"Bearer {demo_viewer_token}"},
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_chat_does_not_log_message_content(demo_client, demo_token, caplog) -> None:
    import logging

    marker = "super-private-question-marker-xyz"
    with caplog.at_level(logging.DEBUG):
        await demo_client.post(
            "/api/v1/chat/messages",
            headers={"Authorization": f"Bearer {demo_token}"},
            json={"message": marker},
        )
    assert marker not in caplog.text
