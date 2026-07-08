from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """The shape every document extraction adapter returns.

    Mirrors the financial engine's ``CalculationResult`` and the AI
    orchestrator's ``RuntimeCompletion`` (text/structured output plus
    confidence and warnings) so the codebase's adapter patterns stay
    consistent.
    """

    text: str
    structured_fields: dict[str, Any]
    confidence: float
    warnings: list[str] = field(default_factory=list)


class DocumentExtractionAdapter(Protocol):
    """The replaceable seam for turning a document's bytes into structured data (ADR 0007)."""

    def extract(self, content: bytes, content_type: str) -> ExtractionResult: ...
