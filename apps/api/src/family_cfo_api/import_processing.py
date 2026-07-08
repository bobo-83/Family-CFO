from __future__ import annotations

import csv
import io
import logging
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from family_cfo_ocr_worker import PdfTextExtractionAdapter
from family_cfo_scheduler import RetryExhaustedError, RetryPolicy, run_with_retry
from sqlalchemy.engine import Engine

from family_cfo_api import repository

logger = logging.getLogger(__name__)

MAX_IMPORT_ATTEMPTS = 3
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y")

_pdf_adapter = PdfTextExtractionAdapter()


def _parse_amount_minor(raw: str) -> int | None:
    cleaned = raw.strip().replace(",", "").replace("$", "")
    if not cleaned:
        return None
    try:
        decimal_value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return int((decimal_value * 100).to_integral_value())


def _parse_date(raw: str) -> date | None:
    cleaned = raw.strip()
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    return None


def _process_csv(engine: Engine, import_record: repository.ImportRecord, file_bytes: bytes) -> None:
    if import_record.account_id is None:
        raise ValueError("CSV imports require an account_id")

    household = repository.get_household(engine, import_record.household_id)
    if household is None:
        raise ValueError(f"household {import_record.household_id} not found")

    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [(name or "").strip().lower() for name in (reader.fieldnames or [])]

    skipped = 0
    for row in reader:
        normalized = {(key or "").strip().lower(): value for key, value in row.items() if key}
        raw_date = normalized.get("date", "")
        raw_amount = normalized.get("amount", "")
        merchant = normalized.get("description") or normalized.get("merchant")

        occurred_at = _parse_date(raw_date) if raw_date else None
        amount_minor = _parse_amount_minor(raw_amount) if raw_amount else None

        if occurred_at is None or amount_minor is None:
            skipped += 1
            continue

        possible_duplicate = repository.transaction_exists(
            engine, import_record.household_id, import_record.account_id, occurred_at, amount_minor
        )

        repository.create_transaction(
            engine,
            household_id=import_record.household_id,
            account_id=import_record.account_id,
            occurred_at=occurred_at,
            amount_minor=amount_minor,
            currency=household.base_currency,
            merchant=merchant,
            description=normalized.get("category"),
            import_source="csv",
            import_id=import_record.id,
            review_state="pending",
            possible_duplicate=possible_duplicate,
        )

    repository.update_import_status(engine, import_record.id, status="needs_review", skipped_row_count=skipped)


def _process_pdf(engine: Engine, import_record: repository.ImportRecord, file_bytes: bytes) -> None:
    result = _pdf_adapter.extract(file_bytes, "application/pdf")

    document = repository.create_document(
        engine,
        household_id=import_record.household_id,
        content_type="application/pdf",
        storage_path="",
        import_id=import_record.id,
    )
    repository.create_document_extraction(
        engine,
        document_id=document.id,
        extraction_type="pdf_text",
        text=result.text,
        structured_fields=result.structured_fields,
        confidence=result.confidence,
        warnings=result.warnings,
    )

    repository.update_import_status(engine, import_record.id, status="needs_review")


def _read_staged_file(staging_dir: str, storage_path: str) -> bytes:
    full_path = os.path.join(staging_dir, storage_path)
    with open(full_path, "rb") as staged_file:
        return staged_file.read()


def _process_one_import(
    engine: Engine, import_record: repository.ImportRecord, file_record: repository.ImportFileRecord, staging_dir: str
) -> None:
    file_bytes = _read_staged_file(staging_dir, file_record.storage_path)

    if import_record.source_type == "csv":
        _process_csv(engine, import_record, file_bytes)
    elif import_record.source_type == "pdf":
        _process_pdf(engine, import_record, file_bytes)
    else:
        raise ValueError(f"no processor for source_type {import_record.source_type!r} yet (OFX/QFX planning only)")


def run_pending_imports_once(engine: Engine, staging_dir: str) -> int:
    """Process every pending, file-uploaded import once. Returns the number processed successfully.

    Called directly by tests (synchronous, deterministic) and wrapped by
    ``family_cfo_scheduler.Job`` for real background polling.
    """
    processed = 0
    for import_record, file_record in repository.list_processable_imports(engine):
        repository.update_import_status(engine, import_record.id, status="processing")

        def attempt(
            engine: Engine = engine, import_record: repository.ImportRecord = import_record, file_record: repository.ImportFileRecord = file_record
        ) -> None:
            _process_one_import(engine, import_record, file_record, staging_dir)

        def on_attempt_failure(
            error: Exception, attempt_number: int, import_record: repository.ImportRecord = import_record
        ) -> None:
            logger.warning(
                "import processing attempt failed import_id=%s attempt=%s error_type=%s",
                import_record.id,
                attempt_number,
                type(error).__name__,
            )
            repository.increment_import_retry_count(engine, import_record.id)

        try:
            run_with_retry(attempt, RetryPolicy(max_attempts=MAX_IMPORT_ATTEMPTS), on_attempt_failure)
            processed += 1
        except RetryExhaustedError as exc:
            repository.update_import_status(
                engine, import_record.id, status="failed", error_message=f"{type(exc.last_error).__name__}: retries exhausted"
            )

    return processed
