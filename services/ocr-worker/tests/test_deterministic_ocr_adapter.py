from family_cfo_ocr_worker.adapter import ExtractionResult
from family_cfo_ocr_worker.deterministic_ocr_adapter import DeterministicOcrAdapter


def test_returns_registered_fixture_for_known_bytes() -> None:
    known_result = ExtractionResult(
        text="Whole Foods",
        structured_fields={"merchant": "Whole Foods", "amount": "24.99"},
        confidence=0.95,
        warnings=[],
    )
    adapter = DeterministicOcrAdapter(fixtures={b"receipt-bytes": known_result})

    result = adapter.extract(b"receipt-bytes", "image/png")

    assert result == known_result


def test_returns_not_available_result_for_unknown_bytes() -> None:
    adapter = DeterministicOcrAdapter()

    result = adapter.extract(b"unknown-image-bytes", "image/jpeg")

    assert result.confidence == 0.0
    assert result.text == ""
    assert result.structured_fields == {}
    assert "OCR is not available" in result.warnings[0]


def test_empty_fixtures_argument_behaves_like_no_fixtures() -> None:
    adapter = DeterministicOcrAdapter(fixtures={})

    result = adapter.extract(b"anything", "image/png")

    assert result.confidence == 0.0
