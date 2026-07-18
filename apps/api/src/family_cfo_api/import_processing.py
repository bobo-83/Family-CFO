from __future__ import annotations

import csv
import io
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from family_cfo_ocr_worker import PdfTextExtractionAdapter
from family_cfo_scheduler import RetryExhaustedError, RetryPolicy, run_with_retry
from sqlalchemy.engine import Engine

from family_cfo_api import ofx_parsing, repository

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

        # ADR 0015 dedupe: an exact content-hash match (same account, date,
        # amount, payee) is skipped — re-uploading the same CSV imports nothing.
        created = repository.create_transaction_deduped(
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
        )
        if not created:
            skipped += 1

    repository.update_import_status(
        engine, import_record.id, status="needs_review", skipped_row_count=skipped
    )


def _process_ofx(engine: Engine, import_record: repository.ImportRecord, file_bytes: bytes) -> None:
    """M34: OFX/QFX statements. FITID feeds the external_id hard-dedupe, so
    re-importing the same or overlapping exports is idempotent."""
    household = repository.get_household(engine, import_record.household_id)
    assert household is not None
    transactions, skipped = ofx_parsing.parse_ofx_transactions(file_bytes)

    for txn in transactions:
        created = repository.create_transaction_deduped(
            engine,
            household_id=import_record.household_id,
            account_id=import_record.account_id,
            occurred_at=txn.posted,
            amount_minor=txn.amount_minor,
            currency=household.base_currency,
            merchant=txn.name,
            description=txn.memo,
            import_source=import_record.source_type,
            import_id=import_record.id,
            review_state="pending",
            external_id=txn.fitid,
        )
        if not created:
            skipped += 1

    repository.update_import_status(
        engine, import_record.id, status="needs_review", skipped_row_count=skipped
    )


# Heuristic statement line: date, payee text, amount at end of line. Parentheses
# or a trailing minus mean negative. Handles "07/03 STARBUCKS 4.50" and
# "2026-07-03  Rent payment  $1,200.00-".
_STATEMENT_LINE = re.compile(
    r"^\s*(?P<date>\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2})\s+"
    r"(?P<payee>.+?)\s+"
    r"(?P<neg_open>\()?\$?(?P<amount>-?[\d,]+\.\d{2})(?P<neg_close>\))?(?P<trail_minus>-)?\s*$"
)


def _parse_statement_line_date(raw: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    # MM/DD without a year: assume the current year.
    try:
        parsed = datetime.strptime(raw, "%m/%d")
        return parsed.replace(year=date.today().year).date()
    except ValueError:
        return None


def _parse_statement_lines(text: str) -> list[tuple[date, str, int]]:
    rows: list[tuple[date, str, int]] = []
    for line in text.splitlines():
        match = _STATEMENT_LINE.match(line)
        if not match:
            continue
        occurred = _parse_statement_line_date(match.group("date"))
        if occurred is None:
            continue
        amount_minor = int(
            (Decimal(match.group("amount").replace(",", "")) * 100).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        if match.group("neg_open") or match.group("trail_minus"):
            amount_minor = -abs(amount_minor)
        rows.append((occurred, match.group("payee").strip(), amount_minor))
    return rows


# --- ADR 0033: read the account's own summary fields off a loan/card statement.
# Conservative label-anchored patterns — a wrong due date is worse than none, so
# each field is only set when its label is found. `_D` accepts MM/DD/YYYY,
# MM/DD/YY, YYYY-MM-DD, and "Aug 8, 2026" style; `_A` a dollars-and-cents amount.
_D = r"(?P<d>[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})"
_A = r"\$?(?P<a>[\d,]+\.\d{2})"
_STMT_DATE_RE = re.compile(
    r"(?:statement\s+closing\s+date|closing\s+date|statement\s+date)\s*[:\-]?\s*" + _D,
    re.IGNORECASE,
)
_PAYMENT_DUE_RE = re.compile(
    r"(?:payment\s+due\s+date|due\s+date|payment\s+due)\s*[:\-]?\s*" + _D, re.IGNORECASE
)
_MIN_PAYMENT_RE = re.compile(
    r"(?:minimum\s+payment\s+due|minimum\s+payment|minimum\s+amount\s+due|min(?:imum)?\s+due)"
    r"\s*[:\-]?\s*" + _A,
    re.IGNORECASE,
)
_NEW_BALANCE_RE = re.compile(
    r"(?:new\s+balance(?:\s+total)?|statement\s+balance|balance\s+owed)\s*[:\-]?\s*" + _A,
    re.IGNORECASE,
)
_LABEL_DATE_FORMATS = (
    "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%Y-%m-%d", "%B %d %Y", "%b %d %Y",
)


@dataclass(frozen=True)
class StatementFields:
    statement_date: date | None = None
    payment_due_date: date | None = None
    minimum_payment_minor: int | None = None
    statement_balance_minor: int | None = None


def _parse_label_date(raw: str) -> date | None:
    cleaned = " ".join(raw.strip().replace(",", "").replace(".", "").split())
    for fmt in _LABEL_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def parse_statement_fields(text: str) -> StatementFields:
    """The account-level summary a loan/card statement prints (ADR 0033): its
    closing date, the payment due date, the minimum payment, and the new balance.
    Every field is independent and optional — a statement that shows none leaves
    the account untouched."""
    def _amt(match: re.Match[str] | None) -> int | None:
        return _parse_amount_minor(match.group("a")) if match else None

    def _dt(match: re.Match[str] | None) -> date | None:
        return _parse_label_date(match.group("d")) if match else None

    return StatementFields(
        statement_date=_dt(_STMT_DATE_RE.search(text)),
        payment_due_date=_dt(_PAYMENT_DUE_RE.search(text)),
        minimum_payment_minor=_amt(_MIN_PAYMENT_RE.search(text)),
        statement_balance_minor=_amt(_NEW_BALANCE_RE.search(text)),
    )


def apply_statement_fields_to_account(
    engine: Engine, import_record: repository.ImportRecord, text: str
) -> None:
    """When an import is tied to a liability account, fold the statement's summary
    into that account (ADR 0033): the due date and minimum payment update the
    account row; the new balance is recorded as a (negative) balance dated by the
    statement's closing date, so an out-of-order upload can't clobber a newer one.
    Assets and account-less imports are left alone."""
    account_id = import_record.account_id
    if account_id is None:
        return
    account = repository.get_account(engine, import_record.household_id, account_id)
    if account is None or account.account_type not in repository.LIABILITY_ACCOUNT_TYPES:
        return
    fields = parse_statement_fields(text)

    updates: dict[str, object] = {}
    if fields.payment_due_date is not None:
        updates["next_payment_due_date"] = fields.payment_due_date
    if fields.minimum_payment_minor is not None and fields.minimum_payment_minor > 0:
        updates["minimum_payment_minor"] = fields.minimum_payment_minor
    if updates:
        repository.update_account(engine, import_record.household_id, account_id, **updates)

    if fields.statement_balance_minor is not None and fields.statement_balance_minor > 0:
        # A liability's balance is what is owed — stored negative (assets positive).
        new_balance = -fields.statement_balance_minor
        latest = next(
            (
                b
                for b in repository.list_account_balances(engine, import_record.household_id)
                if b.account_id == account_id
            ),
            None,
        )
        # Re-importing the same statement must not add a redundant balance row:
        # only record when the latest balance actually differs (ADR 0015 spirit).
        if latest is None or latest.balance_minor != new_balance:
            as_of = (
                datetime.combine(fields.statement_date, datetime.min.time(), tzinfo=UTC)
                if fields.statement_date is not None
                else None
            )
            repository.record_account_balance(engine, account_id, new_balance, as_of=as_of)


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

    # ADR 0033: fold a loan/card statement's summary (due date, minimum payment,
    # new balance) into the account this import belongs to.
    apply_statement_fields_to_account(engine, import_record, result.text)

    # M34: heuristic statement line-items -> pending transactions for review
    # (content-hash deduped). A PDF with no recognizable lines behaves as
    # before: document extraction only.
    household = repository.get_household(engine, import_record.household_id)
    assert household is not None
    skipped = 0
    for occurred_at, payee, amount_minor in _parse_statement_lines(result.text):
        created = repository.create_transaction_deduped(
            engine,
            household_id=import_record.household_id,
            account_id=import_record.account_id,
            occurred_at=occurred_at,
            amount_minor=amount_minor,
            currency=household.base_currency,
            merchant=payee,
            description=None,
            import_source="pdf",
            import_id=import_record.id,
            review_state="pending",
        )
        if not created:
            skipped += 1

    repository.update_import_status(
        engine, import_record.id, status="needs_review", skipped_row_count=skipped
    )


def _read_staged_file(staging_dir: str, storage_path: str) -> bytes:
    full_path = os.path.join(staging_dir, storage_path)
    with open(full_path, "rb") as staged_file:
        return staged_file.read()


def _process_one_import(
    engine: Engine,
    import_record: repository.ImportRecord,
    file_record: repository.ImportFileRecord,
    staging_dir: str,
) -> None:
    file_bytes = _read_staged_file(staging_dir, file_record.storage_path)

    if import_record.source_type == "csv":
        _process_csv(engine, import_record, file_bytes)
    elif import_record.source_type == "pdf":
        _process_pdf(engine, import_record, file_bytes)
    elif import_record.source_type in ("ofx", "qfx"):
        _process_ofx(engine, import_record, file_bytes)
    else:
        raise ValueError(f"no processor for source_type {import_record.source_type!r}")


def run_pending_imports_once(engine: Engine, staging_dir: str) -> int:
    """Process every pending, file-uploaded import once. Returns the number processed successfully.

    Called directly by tests (synchronous, deterministic) and wrapped by
    ``family_cfo_scheduler.Job`` for real background polling.
    """
    processed = 0
    for import_record, file_record in repository.list_processable_imports(engine):
        repository.update_import_status(engine, import_record.id, status="processing")

        def attempt(
            engine: Engine = engine,
            import_record: repository.ImportRecord = import_record,
            file_record: repository.ImportFileRecord = file_record,
        ) -> None:
            _process_one_import(engine, import_record, file_record, staging_dir)

        def on_attempt_failure(
            error: Exception,
            attempt_number: int,
            import_record: repository.ImportRecord = import_record,
        ) -> None:
            logger.warning(
                "import processing attempt failed import_id=%s attempt=%s error_type=%s",
                import_record.id,
                attempt_number,
                type(error).__name__,
            )
            repository.increment_import_retry_count(engine, import_record.id)

        try:
            run_with_retry(
                attempt, RetryPolicy(max_attempts=MAX_IMPORT_ATTEMPTS), on_attempt_failure
            )
            processed += 1
        except RetryExhaustedError as exc:
            repository.update_import_status(
                engine,
                import_record.id,
                status="failed",
                error_message=f"{type(exc.last_error).__name__}: retries exhausted",
            )

    return processed
