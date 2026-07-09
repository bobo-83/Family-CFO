from family_cfo_ocr_worker.adapter import DocumentExtractionAdapter, ExtractionResult
from family_cfo_ocr_worker.deterministic_ocr_adapter import DeterministicOcrAdapter
from family_cfo_ocr_worker.pdf_adapter import PdfTextExtractionAdapter

__all__ = [
    "TesseractOcrAdapter",
    "default_ocr_adapter",
    "tesseract_available",
    "DeterministicOcrAdapter",
    "DocumentExtractionAdapter",
    "ExtractionResult",
    "PdfTextExtractionAdapter",
]

__version__ = "0.1.0"
from family_cfo_ocr_worker.tesseract_ocr_adapter import (
    TesseractOcrAdapter,
    default_ocr_adapter,
    tesseract_available,
)
