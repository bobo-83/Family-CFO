from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from family_cfo_ai_orchestrator.runtime import RuntimeAdapter, RuntimeMessage, ToolSpec

# An app-supplied callback that executes a validated tool and returns a
# JSON-serializable result. It should NEVER raise for bad arguments or missing
# facts -- instead return an error/`missing_input` payload so the model can
# correct itself or ask the user. Raising is reserved for genuine faults.
ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]

DEFAULT_MAX_ITERATIONS = 6


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCallingResult:
    answer: str | None  # None if the loop did not converge within the iteration cap
    completed: bool
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


def run_tool_calling_loop(
    runtime: RuntimeAdapter,
    messages: list[RuntimeMessage],
    tools: list[ToolSpec],
    execute_tool: ToolExecutor,
    *,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    temperature: float = 0.2,
    max_tokens: int = 500,
) -> ToolCallingResult:
    """Drive a bounded model <-> tool exchange.

    The model may request tool calls; each is executed through ``execute_tool``
    and its result fed back, until the model returns a final text answer or the
    iteration cap is hit (in which case ``completed`` is False and the caller
    should fall back to a deterministic response). ``RuntimeUnavailableError``
    from the runtime propagates to the caller.
    """
    conversation = list(messages)
    trace: list[ToolCallRecord] = []

    for _ in range(max_iterations):
        completion = runtime.complete_with_tools(
            conversation, tools, temperature=temperature, max_tokens=max_tokens
        )
        if not completion.wants_tools:
            return ToolCallingResult(answer=completion.text, completed=True, tool_calls=trace)

        conversation.append(
            RuntimeMessage(
                role="assistant", content=completion.text, tool_calls=completion.tool_calls
            )
        )
        for call in completion.tool_calls:
            result = execute_tool(call.name, call.arguments)
            trace.append(ToolCallRecord(name=call.name, arguments=call.arguments, result=result))
            conversation.append(
                RuntimeMessage(role="tool", content=json.dumps(result), tool_call_id=call.id)
            )

    return ToolCallingResult(answer=None, completed=False, tool_calls=trace)
