import pytest
from fpdf import FPDF

from family_cfo_ocr_worker.pdf_adapter import PdfTextExtractionAdapter


def _synthetic_pdf_bytes(lines: list[str]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in lines:
        pdf.cell(0, 10, text=line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def test_extracts_text_from_a_synthetic_pdf() -> None:
    content = _synthetic_pdf_bytes(["Family CFO Statement", "Grocery Store $42.50"])

    result = PdfTextExtractionAdapter().extract(content, "application/pdf")

    assert "Family CFO Statement" in result.text
    assert "Grocery Store" in result.text
    assert result.confidence == 0.4
    assert result.warnings == ["possible_amounts is a naive regex match, not a validated total"]


def test_finds_possible_amounts_via_regex() -> None:
    content = _synthetic_pdf_bytes(["Total due: $1,234.56", "Minimum payment $25.00"])

    result = PdfTextExtractionAdapter().extract(content, "application/pdf")

    assert result.structured_fields["possible_amounts"] == ["$1,234.56", "$25.00"]


def test_blank_pdf_yields_zero_confidence_and_a_warning() -> None:
    pdf = FPDF()
    pdf.add_page()
    content = bytes(pdf.output())

    result = PdfTextExtractionAdapter().extract(content, "application/pdf")

    assert result.text == ""
    assert result.confidence == 0.0
    assert "scanned image PDF" in result.warnings[0]


def test_rejects_non_pdf_content_type() -> None:
    with pytest.raises(ValueError, match="application/pdf"):
        PdfTextExtractionAdapter().extract(b"not a pdf", "image/png")
