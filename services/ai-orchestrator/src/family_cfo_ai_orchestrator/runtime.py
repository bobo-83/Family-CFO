from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class RuntimeMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class RuntimeCompletion:
    text: str
    model: str
    raw: dict[str, Any]


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
