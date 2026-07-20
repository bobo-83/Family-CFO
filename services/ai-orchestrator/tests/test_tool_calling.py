from family_cfo_ai_orchestrator import (
    RuntimeMessage,
    RuntimeToolCompletion,
    ToolCall,
    ToolSpec,
    run_tool_calling_loop,
)


class ScriptedRuntime:
    """A stubbed runtime that returns a fixed sequence of tool-calling turns."""

    def __init__(self, turns: list[RuntimeToolCompletion]) -> None:
        self._turns = turns
        self.calls = 0
        self.seen_messages: list[list[RuntimeMessage]] = []
        self.seen_max_tokens: list[int] = []

    def complete_with_tools(self, messages, tools, *, temperature=0.2, max_tokens=500):
        self.seen_messages.append(list(messages))
        self.seen_max_tokens.append(max_tokens)
        turn = self._turns[self.calls]
        self.calls += 1
        return turn


_TOOLS = [ToolSpec(name="future_value", description="", parameters={})]


def test_loop_executes_tool_then_returns_final_answer() -> None:
    runtime = ScriptedRuntime(
        [
            RuntimeToolCompletion(
                tool_calls=[ToolCall(id="c1", name="future_value", arguments={"amount": 1000})],
                text="",
                model="m",
                raw={},
            ),
            RuntimeToolCompletion(
                tool_calls=[], text="It would grow to $3,207.", model="m", raw={}
            ),
        ]
    )
    executed: list[tuple[str, dict]] = []

    def execute(name, args):
        executed.append((name, args))
        return {"future_value": 3207}

    result = run_tool_calling_loop(runtime, [RuntimeMessage(role="user", content="q")], _TOOLS, execute)

    assert result.completed is True
    assert result.answer == "It would grow to $3,207."
    assert executed == [("future_value", {"amount": 1000})]
    assert [record.name for record in result.tool_calls] == ["future_value"]
    # The tool result was fed back to the model before the final turn.
    assert any(
        m.role == "tool" and "3207" in m.content for m in runtime.seen_messages[-1]
    )


def test_loop_returns_final_answer_immediately_when_no_tools_needed() -> None:
    runtime = ScriptedRuntime(
        [RuntimeToolCompletion(tool_calls=[], text="Your net worth is $5,000.", model="m", raw={})]
    )
    result = run_tool_calling_loop(
        runtime, [RuntimeMessage(role="user", content="q")], _TOOLS, lambda n, a: {}
    )
    assert result.completed is True
    assert result.answer == "Your net worth is $5,000."
    assert result.tool_calls == []


def test_loop_stops_at_iteration_cap_without_converging() -> None:
    # Always asks for another tool call -> never converges.
    always_tool = RuntimeToolCompletion(
        tool_calls=[ToolCall(id="c", name="future_value", arguments={})], text="", model="m", raw={}
    )
    runtime = ScriptedRuntime([always_tool] * 10)

    result = run_tool_calling_loop(
        runtime,
        [RuntimeMessage(role="user", content="q")],
        _TOOLS,
        lambda n, a: {"ok": True},
        max_iterations=3,
    )

    assert result.completed is False
    assert result.answer is None
    assert runtime.calls == 3


def test_missing_input_result_is_fed_back_for_the_model_to_ask() -> None:
    runtime = ScriptedRuntime(
        [
            RuntimeToolCompletion(
                tool_calls=[ToolCall(id="c1", name="project_retirement", arguments={})],
                text="",
                model="m",
                raw={},
            ),
            RuntimeToolCompletion(
                tool_calls=[],
                text="What annual spending should I assume in retirement?",
                model="m",
                raw={},
            ),
        ]
    )

    def execute(name, args):
        return {"error": "missing_input", "missing": "annual_expenses"}

    result = run_tool_calling_loop(
        runtime, [RuntimeMessage(role="user", content="can we retire?")], _TOOLS, execute
    )

    assert result.completed is True
    assert "annual spending" in result.answer
    assert any("missing_input" in m.content for m in runtime.seen_messages[-1])


def test_max_tokens_is_passed_through_to_the_runtime() -> None:
    # A long multi-step answer was truncated at the 500-token default; the caller
    # can lift the ceiling and it must reach the runtime.
    runtime = ScriptedRuntime(
        [RuntimeToolCompletion(tool_calls=[], text="Here is a long plan…", model="m", raw={})]
    )

    run_tool_calling_loop(
        runtime,
        [RuntimeMessage(role="user", content="what's the plan?")],
        _TOOLS,
        lambda name, args: {},
        max_tokens=1200,
    )

    assert runtime.seen_max_tokens == [1200]
