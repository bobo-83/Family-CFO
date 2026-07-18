import os
from datetime import date

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


def test_import_processing_error_fails_after_retries(demo_engine: Engine, tmp_path) -> None:
    # Every valid source_type has a processor since M34, so exercise the
    # retry-then-fail path with a staged file that vanished before processing.
    staging_dir = str(tmp_path)
    import_record = _create_pending_import_with_file(
        demo_engine, staging_dir, b"OFXHEADER:100", filename="statement.ofx", source_type="ofx"
    )
    os.remove(os.path.join(staging_dir, f"{import_record.id}/statement.ofx"))

    processed = import_processing.run_pending_imports_once(demo_engine, staging_dir)

    assert processed == 0
    updated = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.retry_count == import_processing.MAX_IMPORT_ATTEMPTS
    assert updated.error_message is not None


# --- ADR 0033: statement summary -> account (due date, min payment, balance) ---


def test_parse_statement_fields_reads_due_date_min_payment_and_balance() -> None:
    text = (
        "ACME LOAN SERVICING\n"
        "Statement Closing Date: 07/15/2026\n"
        "New Balance: $12,345.67\n"
        "Minimum Payment Due: $78.01\n"
        "Payment Due Date: August 8, 2026\n"
    )
    fields = import_processing.parse_statement_fields(text)
    assert fields.statement_date == date(2026, 7, 15)
    assert fields.payment_due_date == date(2026, 8, 8)
    assert fields.minimum_payment_minor == 7_801
    assert fields.statement_balance_minor == 1_234_567


def test_parse_statement_fields_without_labels_leaves_everything_none() -> None:
    fields = import_processing.parse_statement_fields("Coffee Shop  $4.50\nThanks!")
    assert fields == import_processing.StatementFields()


def test_statement_import_folds_summary_into_the_liability_account(
    demo_engine: Engine,
) -> None:
    loan = repository.create_account(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, name="U.S. Department of Education",
        account_type="student_loan", currency="USD", minimum_payment_minor=5_000,
    )
    import_record = repository.create_import(
        demo_engine, household_id=fixtures.DEMO_HOUSEHOLD_ID, account_id=loan.id,
        source_type="pdf", filename="doe.pdf",
    )
    text = (
        "U.S. Department of Education\n"
        "Statement Closing Date: 07/10/2026\n"
        "New Balance: $9,500.00\n"
        "Minimum Payment Due: $78.01\n"
        "Payment Due Date: 08/08/2026\n"
    )
    import_processing.apply_statement_fields_to_account(demo_engine, import_record, text)

    updated = repository.get_account(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, loan.id)
    assert updated is not None
    assert updated.next_payment_due_date == date(2026, 8, 8)
    assert updated.minimum_payment_minor == 7_801  # refreshed from the statement
    balances = {
        b.account_id: b
        for b in repository.list_account_balances(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    }
    assert balances[loan.id].balance_minor == -950_000  # owed, stored negative


def test_statement_import_leaves_asset_accounts_untouched(demo_engine: Engine) -> None:
    import_record = repository.create_import(
        demo_engine, household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID, source_type="pdf", filename="chk.pdf",
    )
    import_processing.apply_statement_fields_to_account(
        demo_engine, import_record,
        "Payment Due Date: 08/08/2026\nMinimum Payment Due: $78.01\n",
    )
    checking = repository.get_account(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, fixtures.DEMO_CHECKING_ACCOUNT_ID
    )
    assert checking is not None and checking.next_payment_due_date is None


def test_reimporting_the_same_statement_is_idempotent(demo_engine: Engine) -> None:
    """Re-uploading the same loan statement updates the one account it's tied to —
    it never creates a second account, and it doesn't pile up duplicate balance
    rows (ADR 0033)."""
    from family_cfo_api import models
    from sqlalchemy import func, select

    def _account_count() -> int:
        with demo_engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(models.accounts).where(
                    models.accounts.c.household_id == fixtures.DEMO_HOUSEHOLD_ID
                )
            ).scalar_one()

    accounts_before = _account_count()
    loan = repository.create_account(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, name="U.S. Department of Education",
        account_type="student_loan", currency="USD",
    )
    text = (
        "Statement Closing Date: 07/10/2026\n"
        "New Balance: $9,500.00\nPayment Due Date: 08/08/2026\n"
    )

    def _import_once() -> None:
        rec = repository.create_import(
            demo_engine, household_id=fixtures.DEMO_HOUSEHOLD_ID, account_id=loan.id,
            source_type="pdf", filename="doe.pdf",
        )
        import_processing.apply_statement_fields_to_account(demo_engine, rec, text)

    _import_once()
    _import_once()

    # Exactly one new account (the loan), no duplicate.
    assert _account_count() == accounts_before + 1
    # One balance row, not two.
    with demo_engine.connect() as conn:
        balance_rows = conn.execute(
            select(func.count()).select_from(models.account_balances).where(
                models.account_balances.c.account_id == loan.id
            )
        ).scalar_one()
    assert balance_rows == 1
