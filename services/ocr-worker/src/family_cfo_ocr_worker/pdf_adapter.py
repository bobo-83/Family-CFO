from __future__ import annotations

import io
import re

from pypdf import PdfReader

from family_cfo_ocr_worker.adapter import ExtractionResult

_AMOUNT_PATTERN = re.compile(r"\$?\s?\d[\d,]*\.\d{2}")
_MAX_POSSIBLE_AMOUNTS = 10


class PdfTextExtractionAdapter:
    """Real, deterministic text extraction from text-based PDFs via pypdf.

    Does not attempt statement-specific line-item parsing — vendor formats
    vary too much for a heuristic to be trustworthy. A scanned (image-only)
    PDF yields little or no text and is surfaced as low confidence, not an
    error.
    """

    def extract(self, content: bytes, content_type: str) -> ExtractionResult:
        if content_type != "application/pdf":
            raise ValueError(f"PdfTextExtractionAdapter only handles application/pdf, got {content_type!r}")

        reader = PdfReader(io.BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()

        if not text:
            return ExtractionResult(
                text="",
                structured_fields={},
                confidence=0.0,
                warnings=[
                    "no extractable text found; this may be a scanned image PDF requiring OCR"
                ],
            )

        warnings: list[str] = []
        structured_fields: dict[str, object] = {}
        amounts = _AMOUNT_PATTERN.findall(text)
        if amounts:
            structured_fields["possible_amounts"] = amounts[:_MAX_POSSIBLE_AMOUNTS]
            warnings.append("possible_amounts is a naive regex match, not a validated total")

        return ExtractionResult(text=text, structured_fields=structured_fields, confidence=0.4, warnings=warnings)
