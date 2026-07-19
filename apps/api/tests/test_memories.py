"""M57 (ADR 0016): household memory + conversation summarization."""

import pytest

from family_cfo_ai_orchestrator import RuntimeCompletion, RuntimeToolCompletion
from family_cfo_api import ai_memory, repository
from family_cfo_api.api import chat as chat_module
from family_cfo_api.fixtures import DEMO_HOUSEHOLD_ID, DEMO_USER_ID


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def demo_household_id() -> str:
    return DEMO_HOUSEHOLD_ID


@pytest.fixture
def demo_user_id() -> str:
    return DEMO_USER_ID


class _StubExtractorRuntime:
    """Plain-completion stub returning scripted texts for each complete() call."""

    def __init__(self, texts: list[str]):
        self._texts = texts
        self.calls = 0
        self.seen: list[list] = []

    def complete(self, messages, *, temperature=0.2, max_tokens=400):
        self.seen.append(list(messages))
        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        return RuntimeCompletion(text=text, model="stub", raw={})

    def close(self):
        pass


# --- extraction parsing ---


def test_parse_extracted_memories_accepts_valid_json() -> None:
    text = '[{"key": "home_city", "value": "Lives in San Jose."}, {"key": "kids_count", "value": "Has 2 kids."}]'

    pairs = ai_memory.parse_extracted_memories(text)

    assert pairs == [("home_city", "Lives in San Jose."), ("kids_count", "Has 2 kids.")]


def test_parse_extracted_memories_strips_code_fences() -> None:
    text = '```json\n[{"key": "employer", "value": "Works at Acme."}]\n```'

    assert ai_memory.parse_extracted_memories(text) == [("employer", "Works at Acme.")]


def test_parse_extracted_memories_tolerates_garbage() -> None:
    assert ai_memory.parse_extracted_memories("Sure! Here are the facts…") == []
    assert ai_memory.parse_extracted_memories('{"key": "not_a_list"}') == []
    assert ai_memory.parse_extracted_memories('[{"key": "BAD KEY!", "value": "x"}]') == []
    assert ai_memory.parse_extracted_memories('[{"key": "ok"}]') == []


def test_parse_extracted_memories_caps_count() -> None:
    items = ",".join(f'{{"key": "fact_{i}", "value": "v{i}"}}' for i in range(20))

    pairs = ai_memory.parse_extracted_memories(f"[{items}]")

    assert len(pairs) == ai_memory.MAX_MEMORIES_PER_MESSAGE


# --- repository semantics ---


@pytest.mark.anyio
async def test_restated_fact_updates_instead_of_duplicating(demo_engine, demo_household_id) -> None:
    repository.upsert_household_memory(
        demo_engine, demo_household_id, "eating_out_frequency", "Eats out 3 times a week."
    )
    repository.upsert_household_memory(
        demo_engine, demo_household_id, "eating_out_frequency", "Eats out 5 times a week."
    )

    memories = repository.list_household_memories(demo_engine, demo_household_id)

    assert len(memories) == 1
    assert memories[0].value == "Eats out 5 times a week."


@pytest.mark.anyio
async def test_memories_survive_conversation_deletion(
    demo_engine, demo_household_id, demo_user_id
) -> None:
    conversation = repository.create_conversation(
        demo_engine, demo_household_id, demo_user_id, "About us"
    )
    repository.upsert_household_memory(
        demo_engine,
        demo_household_id,
        "home_city",
        "Lives in San Jose.",
        source_conversation_id=conversation.id,
    )

    assert repository.delete_conversation(demo_engine, demo_household_id, conversation.id, demo_user_id)

    memories = repository.list_household_memories(demo_engine, demo_household_id)
    assert [m.value for m in memories] == ["Lives in San Jose."]


# --- summarization ---


@pytest.mark.anyio
async def test_summary_skipped_within_history_window(
    demo_engine, demo_household_id, demo_user_id
) -> None:
    conversation = repository.create_conversation(
        demo_engine, demo_household_id, demo_user_id, "Short"
    )
    runtime = _StubExtractorRuntime(["should not be called"])

    assert not ai_memory.refresh_conversation_summary(runtime, demo_engine, conversation.id)
    assert runtime.calls == 0


@pytest.mark.anyio
async def test_summary_written_past_history_window(
    demo_engine, demo_household_id, demo_user_id
) -> None:
    conversation = repository.create_conversation(
        demo_engine, demo_household_id, demo_user_id, "Long"
    )
    for i in range(6):  # 12 messages total, window is 8
        repository.append_conversation_turn(
            demo_engine,
            conversation_id=conversation.id,
            user_content=f"question {i}",
            assistant_content=f"answer {i}",
            recommendation_id=None,
        )
    runtime = _StubExtractorRuntime(["Discussed a laptop purchase for USD 1,500.00."])

    assert ai_memory.refresh_conversation_summary(runtime, demo_engine, conversation.id)

    stored = repository.get_conversation(demo_engine, demo_household_id, conversation.id, demo_user_id)
    assert stored is not None
    assert stored.summary == "Discussed a laptop purchase for USD 1,500.00."
    # Only the messages OLDER than the window were summarized.
    transcript = runtime.seen[0][-1].content
    assert "question 0" in transcript
    assert "question 5" not in transcript


# --- memories API ---


@pytest.mark.anyio
async def test_memory_api_round_trip(demo_client, demo_token) -> None:
    headers = _headers(demo_token)
    created = await demo_client.post(
        "/api/v1/memories", headers=headers, json={"value": "We rent our home."}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["value"] == "We rent our home."
    assert body["source"] == "manual"

    listed = (await demo_client.get("/api/v1/memories", headers=headers)).json()["memories"]
    assert [m["value"] for m in listed] == ["We rent our home."]

    deleted = await demo_client.delete(f"/api/v1/memories/{body['id']}", headers=headers)
    assert deleted.status_code == 204
    listed = (await demo_client.get("/api/v1/memories", headers=headers)).json()["memories"]
    assert listed == []


@pytest.mark.anyio
async def test_viewer_cannot_manage_memories(demo_client, demo_viewer_token) -> None:
    response = await demo_client.post(
        "/api/v1/memories", headers=_headers(demo_viewer_token), json={"value": "Nope"}
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_delete_unknown_memory_is_404(demo_client, demo_token) -> None:
    response = await demo_client.delete(
        "/api/v1/memories/00000000-0000-0000-0000-000000000000", headers=_headers(demo_token)
    )
    assert response.status_code == 404


# --- chat integration ---


class _RecordingScriptedRuntime:
    def __init__(self, turns):
        self._turns = turns
        self._i = 0
        self.seen_messages: list[list] = []

    def complete_with_tools(self, messages, tools, *, temperature=0.2, max_tokens=400):
        self.seen_messages.append(list(messages))
        turn = self._turns[self._i]
        self._i += 1
        return turn

    def close(self):
        pass


@pytest.mark.anyio
async def test_chat_injects_and_grounds_memories(
    demo_client, demo_engine, demo_token, demo_household_id, monkeypatch
) -> None:
    """A remembered figure can be echoed without a tool call — it is grounded context."""
    repository.upsert_household_memory(
        demo_engine, demo_household_id, "daycare_cost", "Daycare costs USD 1,250.00 a month."
    )
    runtime = _RecordingScriptedRuntime(
        [
            RuntimeToolCompletion(
                tool_calls=[],
                text="Daycare at USD 1,250.00 a month is your biggest fixed cost.",
                model="stub",
                raw={},
            )
        ]
    )
    monkeypatch.setattr(chat_module, "select_tool_runtime", lambda engine, household_id: runtime)
    monkeypatch.setattr(ai_memory, "select_tool_runtime", lambda *a, **k: None)

    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers=_headers(demo_token),
        json={"message": "What is our biggest fixed cost?"},
    )

    assert response.status_code == 200
    assert "1,250.00" in response.json()["recommendation"]["answer"]
    contents = [m.content for m in runtime.seen_messages[0]]
    assert any("Known household facts" in c and "Daycare costs USD 1,250.00" in c for c in contents)


@pytest.mark.anyio
async def test_chat_injects_and_grounds_conversation_summary(
    demo_client, demo_engine, demo_token, demo_household_id, demo_user_id, monkeypatch
) -> None:
    conversation = repository.create_conversation(
        demo_engine, demo_household_id, demo_user_id, "Long thread"
    )
    repository.append_conversation_turn(
        demo_engine,
        conversation_id=conversation.id,
        user_content="hello",
        assistant_content="hi",
        recommendation_id=None,
    )
    repository.set_conversation_summary(
        demo_engine, conversation.id, "Earlier we discussed a USD 7,700.00 kitchen remodel."
    )
    runtime = _RecordingScriptedRuntime(
        [
            RuntimeToolCompletion(
                tool_calls=[],
                text="As discussed, the USD 7,700.00 remodel is the priority.",
                model="stub",
                raw={},
            )
        ]
    )
    monkeypatch.setattr(chat_module, "select_tool_runtime", lambda engine, household_id: runtime)
    monkeypatch.setattr(ai_memory, "select_tool_runtime", lambda *a, **k: None)

    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers=_headers(demo_token),
        json={"conversation_id": conversation.id, "message": "What should we prioritize?"},
    )

    assert response.status_code == 200
    assert "7,700.00" in response.json()["recommendation"]["answer"]
    contents = [m.content for m in runtime.seen_messages[0]]
    assert any("Earlier in this conversation" in c and "7,700.00" in c for c in contents)


@pytest.mark.anyio
async def test_chat_schedules_memory_extraction(
    demo_client, demo_engine, demo_token, demo_household_id, monkeypatch
) -> None:
    """The post-response task extracts facts even on the deterministic path."""
    extractor = _StubExtractorRuntime(['[{"key": "home_city", "value": "Lives in San Jose."}]'])
    monkeypatch.setattr(ai_memory, "select_tool_runtime", lambda *a, **k: extractor)

    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers=_headers(demo_token),
        json={"message": "We live in San Jose by the way."},
    )

    assert response.status_code == 200
    memories = repository.list_household_memories(demo_engine, demo_household_id)
    assert [m.value for m in memories] == ["Lives in San Jose."]
    assert memories[0].source == "chat"


# --- backfill ---


@pytest.mark.anyio
async def test_memory_backfill_runs_once(
    demo_engine, demo_household_id, demo_user_id, monkeypatch
) -> None:
    conversation = repository.create_conversation(
        demo_engine, demo_household_id, demo_user_id, "Old thread"
    )
    repository.append_conversation_turn(
        demo_engine,
        conversation_id=conversation.id,
        user_content="We have two kids in elementary school.",
        assistant_content="Noted!",
        recommendation_id=None,
    )
    extractor = _StubExtractorRuntime(['[{"key": "kids_count", "value": "Has two kids."}]'])
    monkeypatch.setattr(ai_memory, "select_tool_runtime", lambda *a, **k: extractor)

    assert ai_memory.run_memory_backfill_once(demo_engine) == 1
    assert [
        m.value for m in repository.list_household_memories(demo_engine, demo_household_id)
    ] == ["Has two kids."]

    # Second run is a no-op thanks to the marker (and the marker is never listed).
    assert ai_memory.run_memory_backfill_once(demo_engine) == 0
    assert extractor.calls == 1
