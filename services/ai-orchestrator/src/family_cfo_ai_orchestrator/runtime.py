from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RuntimeMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    # Set on an assistant message that requested tool calls.
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Set on a "tool" message carrying a tool's result back to the model.
    tool_call_id: str | None = None
    # Optional attached image as a data URL (data:image/jpeg;base64,...) for
    # vision-capable runtimes; adapters render OpenAI multimodal content parts.
    image_data_url: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeCompletion:
    text: str
    model: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool the model may call, described by a JSON-schema parameter object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RuntimeToolCompletion:
    """One turn of a tool-calling exchange: either tool calls to run, or a final answer."""

    tool_calls: list[ToolCall]
    text: str
    model: str
    raw: dict[str, Any]

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class RuntimeUnavailableError(RuntimeError):
    """Raised when a runtime adapter cannot produce a completion after retries."""


class RuntimeAdapter(Protocol):
    """The replaceable seam between the API and any local reasoning model (ADR 0004, ADR 0007).

    Implementations must not raise on a single transient failure; they should
    retry internally and raise ``RuntimeUnavailableError`` only once retries
    are exhausted.
    """

    def complete(
        self,
        messages: list[RuntimeMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 400,
    ) -> RuntimeCompletion: ...

    def complete_with_tools(
        self,
        messages: list[RuntimeMessage],
        tools: list[ToolSpec],
        *,
        temperature: float = 0.2,
        max_tokens: int = 400,
    ) -> RuntimeToolCompletion: ...
