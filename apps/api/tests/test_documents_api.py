import pytest


@pytest.mark.anyio
async def test_create_document_requires_authentication(demo_client) -> None:
    response = await demo_client.post(
        "/api/v1/documents", files={"file": ("x.pdf", b"data", "application/pdf")}
    )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_upload_pdf_document_extracts_real_text(demo_client, demo_token) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, text="Receipt total: $12.34", new_x="LMARGIN", new_y="NEXT")
    pdf_bytes = bytes(pdf.output())

    response = await demo_client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {demo_token}"},
        files={"file": ("receipt.pdf", pdf_bytes, "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["extraction_type"] == "pdf_text"
    assert "12.34" in body["extraction"]["text"]
    assert body["extraction"]["confidence"] == 0.4


@pytest.mark.anyio
async def test_upload_image_document_uses_deterministic_ocr_stub(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {demo_token}"},
        files={"file": ("receipt.png", b"not-a-real-image", "image/png")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["extraction_type"] == "ocr"
    assert body["extraction"]["confidence"] == 0.0
    assert "OCR is not available" in body["extraction"]["warnings"][0]


@pytest.mark.anyio
async def test_unsupported_content_type_returns_400(demo_client, demo_token) -> None:
    response = await demo_client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {demo_token}"},
        files={"file": ("statement.ofx", b"OFXHEADER:100", "application/x-ofx")},
    )

    assert response.status_code == 400


@pytest.mark.anyio
async def test_list_documents_returns_uploaded_documents(demo_client, demo_token) -> None:
    await demo_client.post(
        "/api/v1/documents",
        headers={"Authorization": f"Bearer {demo_token}"},
        files={"file": ("receipt.png", b"some-bytes", "image/png")},
    )

    response = await demo_client.get(
        "/api/v1/documents", headers={"Authorization": f"Bearer {demo_token}"}
    )

    assert response.status_code == 200
    assert len(response.json()["documents"]) == 1
