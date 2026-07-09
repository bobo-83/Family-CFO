import pytest
from sqlalchemy import select

from family_cfo_ai_orchestrator import RuntimeToolCompletion, ToolCall
from family_cfo_api import models
from family_cfo_api.api import chat as chat_module


class _ScriptedRuntime:
    def __init__(self, turns):
        self._turns = turns
        self._i = 0

    def complete_with_tools(self, messages, tools, *, temperature=0.2, max_tokens=400):
        turn = self._turns[self._i]
        self._i += 1
        return turn

    def close(self):
        pass


def _install_runtime(monkeypatch, turns):
    runtime = _ScriptedRuntime(turns)
    monkeypatch.setattr(chat_module, "select_tool_runtime", lambda engine, household_id: runtime)
    return runtime


async def _explanation_source(engine, recommendation_id):
    with engine.connect() as conn:
        row = (
            conn.execute(
                select(models.recommendations).where(
                    models.recommendations.c.id == recommendation_id
                )
            )
            .mappings()
            .first()
        )
    return row["explanation_source"]


@pytest.mark.anyio
async def test_agentic_answer_is_used_when_grounded(
    demo_client, demo_engine, demo_token, monkeypatch
) -> None:
    _install_runtime(
        monkeypatch,
        [
            RuntimeToolCompletion(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="future_value",
                        arguments={
                            "present_value_minor": 100_000,
                            "annual_return_rate": 0.06,
                            "years": 20,
                        },
                    )
                ],
                text="",
                model="stub",
                raw={},
            ),
            RuntimeToolCompletion(
                tool_calls=[],
                text="Invested for 20 years it could grow to USD 3,207.14.",
                model="stub",
                raw={},
            ),
        ],
    )

    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "What could $1000 grow to?"},
    )

    assert response.status_code == 200
    recommendation = response.json()["recommendation"]
    assert recommendation["answer"] == "Invested for 20 years it could grow to USD 3,207.14."
    assert recommendation["calculation_refs"]
    assert await _explanation_source(demo_engine, recommendation["id"]) == "agentic_tool_calling"


@pytest.mark.anyio
async def test_ungrounded_number_falls_back_to_deterministic(
    demo_client, demo_engine, demo_token, monkeypatch
) -> None:
    _install_runtime(
        monkeypatch,
        [
            RuntimeToolCompletion(
                tool_calls=[],
                text="You have exactly USD 999,999.00 in net worth.",
                model="stub",
                raw={},
            )
        ],
    )

    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "How are we doing?"},
    )

    assert response.status_code == 200
    recommendation = response.json()["recommendation"]
    # The fabricated figure was rejected; the deterministic snapshot answered instead.
    assert "999,999" not in recommendation["answer"]
    assert await _explanation_source(demo_engine, recommendation["id"]) == "deterministic_stub"


@pytest.mark.anyio
async def test_non_converging_loop_falls_back_to_deterministic(
    demo_client, demo_engine, demo_token, monkeypatch
) -> None:
    forever = RuntimeToolCompletion(
        tool_calls=[ToolCall(id="c", name="get_net_worth", arguments={})],
        text="",
        model="stub",
        raw={},
    )
    _install_runtime(monkeypatch, [forever] * 20)

    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "loop forever"},
    )

    assert response.status_code == 200
    recommendation = response.json()["recommendation"]
    assert await _explanation_source(demo_engine, recommendation["id"]) == "deterministic_stub"


@pytest.mark.anyio
async def test_agentic_answer_reports_which_model_answered(
    demo_client, demo_engine, demo_token, monkeypatch
) -> None:
    # Enable a runtime config so resolve_ai_config carries a model id.
    await demo_client.put(
        "/api/v1/ai/runtime",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={
            "provider": "vllm",
            "base_url": "http://vllm:8000",
            "model": "Qwen/Qwen2.5-32B-Instruct",
            "enabled": True,
        },
    )
    _install_runtime(
        monkeypatch,
        [RuntimeToolCompletion(tool_calls=[], text="All good.", model="stub", raw={})],
    )

    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "How are we doing?"},
    )
    rec = response.json()["recommendation"]
    assert rec["answered_by"] == "Qwen/Qwen2.5-32B-Instruct"


@pytest.mark.anyio
async def test_deterministic_answer_reports_no_model(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/chat/messages",
        headers={"Authorization": f"Bearer {demo_token}"},
        json={"message": "How are we doing?"},
    )
    assert response.json()["recommendation"]["answered_by"] is None


@pytest.mark.anyio
async def test_follow_up_includes_conversation_history(
    demo_client, demo_engine, demo_token, monkeypatch
) -> None:
    """M30: prior turns are sent to the model, so follow-ups have context."""
    runtime = _ScriptedRuntime(
        [
            RuntimeToolCompletion(
                tool_calls=[], text="A Mac mini costs money.", model="stub", raw={}
            ),
            RuntimeToolCompletion(
                tool_calls=[], text="Following up on the Mac mini.", model="stub", raw={}
            ),
        ]
    )
    runtime.seen_messages = []

    class _Recorder(_ScriptedRuntime):
        pass

    def complete_with_tools(messages, tools, *, temperature=0.2, max_tokens=400):
        runtime.seen_messages.append(list(messages))
        turn = runtime._turns[runtime._i]
        runtime._i += 1
        return turn

    runtime.complete_with_tools = complete_with_tools
    monkeypatch.setattr(chat_module, "select_tool_runtime", lambda engine, household_id: runtime)

    headers = {"Authorization": f"Bearer {demo_token}"}
    first = await demo_client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"message": "What is the price of a Mac mini at Best Buy?"},
    )
    conversation_id = first.json()["conversation_id"]

    second = await demo_client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"conversation_id": conversation_id, "message": "How about at Apple.com?"},
    )
    assert second.status_code == 200

    # The second request's message list contains the prior user question and
    # the prior assistant answer, between the system prompt and the new message.
    second_messages = runtime.seen_messages[1]
    contents = [m.content for m in second_messages]
    assert any("Mac mini at Best Buy" in c for c in contents)
    assert any("A Mac mini costs money." in c for c in contents)
    roles = [m.role for m in second_messages]
    assert roles[0] == "system" and roles[-1] == "user"


@pytest.mark.anyio
async def test_history_numbers_are_grounded_in_follow_ups(
    demo_client, demo_engine, demo_token, monkeypatch
) -> None:
    """Echoing a figure from an earlier grounded answer must not trip the guardrail."""
    runtime = _ScriptedRuntime(
        [
            RuntimeToolCompletion(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="future_value",
                        arguments={
                            "present_value_minor": 100_000,
                            "annual_return_rate": 0.06,
                            "years": 20,
                        },
                    )
                ],
                text="",
                model="stub",
                raw={},
            ),
            RuntimeToolCompletion(
                tool_calls=[], text="It could grow to USD 3,207.14.", model="stub", raw={}
            ),
            # Follow-up echoes the earlier figure WITHOUT calling any tool.
            RuntimeToolCompletion(
                tool_calls=[],
                text="As I said, USD 3,207.14 after 20 years.",
                model="stub",
                raw={},
            ),
        ]
    )
    monkeypatch.setattr(chat_module, "select_tool_runtime", lambda engine, household_id: runtime)

    headers = {"Authorization": f"Bearer {demo_token}"}
    first = await demo_client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"message": "What could $1000 grow to in 20 years at 6%?"},
    )
    conversation_id = first.json()["conversation_id"]

    second = await demo_client.post(
        "/api/v1/chat/messages",
        headers=headers,
        json={"conversation_id": conversation_id, "message": "Remind me of that number?"},
    )
    rec = second.json()["recommendation"]
    # Without history grounding this would have fallen back deterministically.
    assert "3,207.14" in rec["answer"]
