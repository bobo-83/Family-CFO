import os

from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, import_processing, repository


def _stage_file(staging_dir: str, import_id: str, filename: str, content: bytes) -> str:
    storage_path = f"{import_id}/{filename}"
    full_path = os.path.join(staging_dir, storage_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(content)
    return storage_path


def _create_pending_import_with_file(
    demo_engine: Engine,
    staging_dir: str,
    content: bytes,
    filename: str = "statement.csv",
    source_type: str = "csv",
) -> repository.ImportRecord:
    import_record = repository.create_import(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
        source_type=source_type,
        filename=filename,
    )
    storage_path = _stage_file(staging_dir, import_record.id, filename, content)
    repository.create_import_file(
        demo_engine,
        import_id=import_record.id,
        storage_path=storage_path,
        content_type="text/csv" if source_type == "csv" else "application/pdf",
        size_bytes=len(content),
    )
    return import_record


def test_processes_a_valid_csv_and_creates_pending_transactions(
    demo_engine: Engine, tmp_path
) -> None:
    staging_dir = str(tmp_path)
    csv_content = (
        b"date,amount,description\n2026-01-05,-42.50,Grocery Store\n2026-01-06,-10.00,Coffee Shop\n"
    )
    import_record = _create_pending_import_with_file(demo_engine, staging_dir, csv_content)

    processed = import_processing.run_pending_imports_once(demo_engine, staging_dir)

    assert processed == 1
    updated = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id)
    assert updated is not None
    assert updated.status == "needs_review"
    assert updated.skipped_row_count == 0

    transactions = repository.list_transactions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    imported = [t for t in transactions if t.merchant in {"Grocery Store", "Coffee Shop"}]
    assert len(imported) == 2


def test_skips_malformed_rows_without_failing_the_import(demo_engine: Engine, tmp_path) -> None:
    staging_dir = str(tmp_path)
    csv_content = (
        b"date,amount,description\n"
        b"2026-01-05,-42.50,Grocery Store\n"
        b"not-a-date,-10.00,Bad Row\n"
        b"2026-01-07,not-a-number,Another Bad Row\n"
    )
    import_record = _create_pending_import_with_file(demo_engine, staging_dir, csv_content)

    import_processing.run_pending_imports_once(demo_engine, staging_dir)

    updated = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id)
    assert updated is not None
    assert updated.status == "needs_review"
    assert updated.skipped_row_count == 2

    transactions = repository.list_transactions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert any(t.merchant == "Grocery Store" for t in transactions)
    assert not any(t.merchant in {"Bad Row", "Another Bad Row"} for t in transactions)


def test_reimporting_the_same_csv_dedupes(demo_engine: Engine, tmp_path) -> None:
    """ADR 0015: an exact content-hash match is skipped, so re-uploading the
    same CSV imports nothing new (previously it duplicated every row)."""
    staging_dir = str(tmp_path)
    csv_content = b"date,amount,description\n2026-01-05,-42.50,Grocery Store\n"

    first_import = _create_pending_import_with_file(
        demo_engine, staging_dir, csv_content, filename="a.csv"
    )
    import_processing.run_pending_imports_once(demo_engine, staging_dir)

    second_import = _create_pending_import_with_file(
        demo_engine, staging_dir, csv_content, filename="b.csv"
    )
    import_processing.run_pending_imports_once(demo_engine, staging_dir)

    assert first_import.id != second_import.id
    transactions = repository.list_transactions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    grocery_rows = [t for t in transactions if t.merchant == "Grocery Store"]
    assert len(grocery_rows) == 1
    # The second import recorded the row as skipped, not silently dropped.
    second = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, second_import.id)
    assert second is not None and second.skipped_row_count == 1


def test_pdf_import_creates_a_document_extraction_not_transactions(
    demo_engine: Engine, tmp_path
) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, text="Statement total: $99.99", new_x="LMARGIN", new_y="NEXT")
    pdf_bytes = bytes(pdf.output())

    staging_dir = str(tmp_path)
    import_record = _create_pending_import_with_file(
        demo_engine, staging_dir, pdf_bytes, filename="statement.pdf", source_type="pdf"
    )

    import_processing.run_pending_imports_once(demo_engine, staging_dir)

    updated = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id)
    assert updated is not None
    assert updated.status == "needs_review"

    documents = repository.list_documents_with_extractions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert len(documents) == 1
    _document, extraction = documents[0]
    assert extraction is not None
    assert "99.99" in extraction.text


def test_import_with_no_processor_fails_after_retries(demo_engine: Engine, tmp_path) -> None:
    staging_dir = str(tmp_path)
    import_record = _create_pending_import_with_file(
        demo_engine, staging_dir, b"OFXHEADER:100", filename="statement.ofx", source_type="ofx"
    )

    processed = import_processing.run_pending_imports_once(demo_engine, staging_dir)

    assert processed == 0
    updated = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.retry_count == import_processing.MAX_IMPORT_ATTEMPTS
    assert updated.error_message is not None
