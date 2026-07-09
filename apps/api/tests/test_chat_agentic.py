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
