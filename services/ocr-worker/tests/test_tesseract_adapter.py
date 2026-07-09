import shutil
from unittest.mock import patch

import pytest

from family_cfo_ocr_worker import (
    DeterministicOcrAdapter,
    TesseractOcrAdapter,
    default_ocr_adapter,
)


def test_factory_falls_back_when_binary_absent() -> None:
    with patch("family_cfo_ocr_worker.tesseract_ocr_adapter.shutil.which", return_value=None):
        assert isinstance(default_ocr_adapter(), DeterministicOcrAdapter)
    with patch(
        "family_cfo_ocr_worker.tesseract_ocr_adapter.shutil.which", return_value="/usr/bin/tesseract"
    ):
        assert isinstance(default_ocr_adapter(), TesseractOcrAdapter)


def test_adapter_reports_failure_as_warning_not_exception() -> None:
    with patch(
        "family_cfo_ocr_worker.tesseract_ocr_adapter.subprocess.run",
        side_effect=OSError("missing"),
    ):
        result = TesseractOcrAdapter().extract(b"fake-image", "image/png")
    assert result.text == "" and result.confidence == 0.0
    assert any("failed" in w for w in result.warnings)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_real_tesseract_reads_an_image() -> None:  # pragma: no cover - env dependent
    from PIL import Image, ImageDraw
    import io

    img = Image.new("RGB", (300, 60), "white")
    ImageDraw.Draw(img).text((10, 10), "TOTAL 42.50", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = TesseractOcrAdapter().extract(buf.getvalue(), "image/png")
    assert "42" in result.text
