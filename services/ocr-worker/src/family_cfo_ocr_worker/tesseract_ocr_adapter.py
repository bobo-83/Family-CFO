"""Real OCR via the tesseract binary (M34).

Selected automatically by ``default_ocr_adapter()`` when tesseract is on PATH
(the Docker image installs it); otherwise the deterministic test adapter keeps
the pipeline hermetic. Runs `tesseract stdin stdout` — no temp files, image
bytes in, recognized text out.
"""

from __future__ import annotations

import shutil
import subprocess

from family_cfo_ocr_worker.adapter import DocumentExtractionAdapter, ExtractionResult
from family_cfo_ocr_worker.deterministic_ocr_adapter import DeterministicOcrAdapter

_TIMEOUT_SECONDS = 60


class TesseractOcrAdapter:
    def extract(self, content: bytes, content_type: str) -> ExtractionResult:
        try:
            result = subprocess.run(
                ["tesseract", "stdin", "stdout"],
                input=content,
                capture_output=True,
                timeout=_TIMEOUT_SECONDS,
                check=True,
            )
            text = result.stdout.decode("utf-8", errors="replace").strip()
        except (subprocess.SubprocessError, OSError) as exc:
            return ExtractionResult(
                text="",
                structured_fields={},
                confidence=0.0,
                warnings=[f"tesseract OCR failed: {exc.__class__.__name__}"],
            )
        return ExtractionResult(
            text=text,
            structured_fields={},
            # Tesseract's per-word confidences are not surfaced by this simple
            # invocation; a fixed conservative value keeps downstream honest.
            confidence=0.6 if text else 0.0,
            warnings=[] if text else ["tesseract produced no text for this image"],
        )


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def default_ocr_adapter() -> DocumentExtractionAdapter:
    """Real OCR when the binary exists; deterministic fallback otherwise."""
    return TesseractOcrAdapter() if tesseract_available() else DeterministicOcrAdapter()
