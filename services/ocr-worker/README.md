# OCR Worker

The OCR worker processes receipts, bills, statements, and imported documents.

Responsibilities:

- OCR
- Structured extraction
- Confidence scoring
- Human review queues
- Redaction support

The worker outputs structured JSON and never provides financial advice.

## M7 Scope

Implemented as the `family_cfo_ocr_worker` package. It has no database or HTTP dependency —
callers (see `apps/api/src/family_cfo_api/import_processing.py` and
`apps/api/src/family_cfo_api/api/documents.py`) select an adapter and persist its output.

- `DocumentExtractionAdapter`: a `Protocol` with one method, `extract(content: bytes, content_type: str) -> ExtractionResult`. `ExtractionResult` carries `text`, `structured_fields`, `confidence`, and `warnings` — deliberately mirroring the financial engine's `CalculationResult` and the AI orchestrator's `RuntimeCompletion` shape for consistency across the codebase's adapter patterns.
- `PdfTextExtractionAdapter` (real): extracts text from text-based PDFs via `pypdf` (pure Python, no system binary). Does not attempt statement-specific line-item parsing — vendor formats vary too much for a heuristic to be trustworthy. Returns raw text plus a naive regex "possible amounts" hint (`confidence = 0.4`) or, for a scanned/image-only PDF with no extractable text, an empty result with `confidence = 0.0` and a warning.
- `TesseractOcrAdapter` (M34): the real OCR engine — shells to the `tesseract` binary (installed in the Docker image). `default_ocr_adapter()` selects it automatically when `tesseract` is on `PATH`, falling back to the deterministic adapter otherwise.
- `DeterministicOcrAdapter` (test/fallback): construct it with a `fixtures: dict[bytes, ExtractionResult]` mapping; known content returns its registered result, unknown content returns a fixed `confidence = 0.0` "OCR is not available in this deployment; manual entry required" result — never a fabricated guess. Same interface, so it's swappable (ADR 0007).

## Assumptions and Limitations

- In the Docker image, `apps/api`'s document upload route uses the Tesseract adapter for images; in tests it uses the deterministic adapter (no fabricated guesses).
- `PdfTextExtractionAdapter` handles text-based PDFs directly; a scanned (image-only) PDF falls to OCR via Tesseract.
- Confidence values are fixed constants (`0.4` for PDF text with a regex hint, `0.0` for no result), not a calibrated score — there's no model to calibrate against yet.

## Tests

```bash
cd services/ocr-worker
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
```

Tests use `fpdf2` (dev-only dependency) to generate synthetic PDF fixtures — never real documents.
