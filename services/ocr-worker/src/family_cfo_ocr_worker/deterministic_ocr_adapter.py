from __future__ import annotations

from family_cfo_ocr_worker.adapter import ExtractionResult

_NOT_AVAILABLE_RESULT = ExtractionResult(
    text="",
    structured_fields={},
    confidence=0.0,
    warnings=["OCR is not available in this deployment; manual entry required"],
)


class DeterministicOcrAdapter:
    """Test-only stand-in for a real OCR engine (Tesseract, Apple Vision, ...).

    No real OCR is performed. Known fixture bytes return their registered
    result; anything else returns a fixed, honest "not available" result
    rather than a fabricated guess. A real adapter behind the same
    ``DocumentExtractionAdapter`` interface is future work.
    """

    def __init__(self, fixtures: dict[bytes, ExtractionResult] | None = None) -> None:
        self._fixtures = dict(fixtures) if fixtures else {}

    def extract(self, content: bytes, content_type: str) -> ExtractionResult:
        return self._fixtures.get(content, _NOT_AVAILABLE_RESULT)
