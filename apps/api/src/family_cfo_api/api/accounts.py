from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, rights, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_right
from family_cfo_api.schemas import (
    Account,
    AccountBalanceCreateRequest,
    AccountCreateRequest,
    AccountListResponse,
    AccountScanRequest,
    AccountScanResult,
    AccountUpdateRequest,
    CardStatement,
    CardStatementCreateRequest,
    CardStatementListResponse,
    CardStatementPaidRequest,
    CardStatementScanLine,
    CardStatementScanRequest,
    CardStatementScanResult,
    ErrorResponse,
    LoanScanRequest,
    LoanScanResult,
    StatementLinesReplaceRequest,
    StatementReconciliation,
    UnaccountedTransaction,
)
from family_cfo_api.schemas import Money as MoneySchema
from family_cfo_api.schemas import (
    StatementLine as StatementLineSchema,
)

router = APIRouter(tags=["Accounts"])


_LOAN_PROMPT = (
    "This image is a loan or car-lease statement. Extract ONLY a JSON object, no "
    "prose: {"
    '"lender": lender or account name string or null, '
    '"monthly_payment": the total regular monthly payment amount as a number or null, '
    '"payoff_balance": the current payoff or principal balance as a number, or null '
    "if not shown (a lease usually has no payoff), "
    '"payments_remaining": number of payments left as an integer, or null if not stated, '
    '"statement_date": the statement date in YYYY-MM-DD, or null, '
    '"payment_due_date": the NEXT payment due date in YYYY-MM-DD, or null, '
    '"maturity_date": the lease maturity/end date or loan final-payment date in '
    "YYYY-MM-DD, or null, "
    '"apr": annual interest or percentage rate as a number or null, '
    '"is_lease": true if this is a lease, false if a loan}. '
    "Use null for anything not shown. Do not guess."
)


# ADR 0057: read an ASSET-account statement (HSA, savings, brokerage …) into
# candidates for the add-account form. Same pattern as the loan scan.
_ACCOUNT_PROMPT = (
    "This image is one page of a financial account statement (e.g. an HSA, "
    "savings, checking, brokerage, retirement, or 529 statement). Extract ONLY "
    "a JSON object, no prose: {"
    '"account_name": the institution and/or account name as printed, or null, '
    '"account_type": one of "checking", "savings", "hsa", "brokerage", '
    '"retirement", "529", "real_estate", "other_asset", or null if unclear, '
    '"cash_balance": the ending/closing CASH balance as a number, or null if '
    "this page shows none, "
    '"investment_value": the investment-portfolio closing/total value as a '
    "number (e.g. an HSA's invested funds, often labeled Closing Account "
    "Value), or null if this page shows none, "
    '"statement_date": the statement (or as-of) date in YYYY-MM-DD, or null}. '
    "An HSA or brokerage statement often shows CASH on one page and "
    "INVESTMENTS on another — report only what THIS page shows; never add "
    "them together yourself. Use null for anything not shown. Do not guess."
)

# Model vocabulary → the app's asset AccountType; synonyms it tends to emit.
_ACCOUNT_TYPE_ALIASES = {
    "checking": "checking",
    "savings": "savings",
    "hsa": "hsa",
    "health savings": "hsa",
    "health savings account": "hsa",
    "brokerage": "brokerage",
    "investment": "brokerage",
    "retirement": "retirement",
    "401k": "retirement",
    "403b": "retirement",
    "ira": "retirement",
    "roth ira": "retirement",
    "529": "529",
    "college savings": "529",
    "cd": "savings",
    "certificate of deposit": "savings",
    "money market": "savings",
    "real_estate": "real_estate",
    "real estate": "real_estate",
    "other_asset": "other_asset",
}


_ACCOUNT_SCAN_NOTE = (
    "Read by the on-box photo model — CONFIRM every value before saving. "
    "Nothing is stored until you save."
)


def _account_scan_total(cash: int | None, investment: int | None) -> int | None:
    if cash is None and investment is None:
        return None
    return (cash or 0) + (investment or 0)


def _account_scan_note(cash: int | None, investment: int | None) -> str:
    # An HSA/brokerage statement splits cash and investments; when both were
    # read, spell the total out so the user can sanity-check the sum.
    if cash is not None and investment is not None:
        return (
            f"Balance = ${cash / 100:,.2f} cash + ${investment / 100:,.2f} invested. "
            + _ACCOUNT_SCAN_NOTE
        )
    if investment is not None and cash is None:
        return "Balance is the invested value (no cash balance found). " + _ACCOUNT_SCAN_NOTE
    return _ACCOUNT_SCAN_NOTE


_CARD_STATEMENT_PROMPT = (
    "You are reading one page of a CREDIT CARD statement. Return ONLY compact "
    "JSON with the keys: statement_balance (the NEW BALANCE for this cycle — not "
    "the available credit, not the previous balance), minimum_due (the minimum "
    "payment due), due_date (payment due date, YYYY-MM-DD), period_start and "
    "period_end (the billing period, YYYY-MM-DD), and transactions (the rows of "
    "the transaction table on THIS page, [] if this page has no table). "
    "Each transaction is {\"date\": YYYY-MM-DD, \"description\": the merchant or "
    "payee text, \"amount\": a signed number}. "
    "SIGN CONVENTION — this is important and is NOT how the statement prints it: "
    "a PURCHASE, CHARGE, FEE or INTEREST charge must be NEGATIVE (a $45.00 "
    "purchase printed as 45.00 becomes -45.00), and a PAYMENT, REFUND, CREDIT or "
    "reversal must be POSITIVE. Statements print charges as positive numbers in "
    "the Amount column; flip them. "
    "If a row prints only month and day, use the year of the billing period "
    "shown on the statement. Skip running-balance, subtotal and total rows — "
    "only real transactions. Use null for any summary value you cannot read, "
    "and never guess a value that is not printed on the statement."
)

_CARD_SCAN_NOTE = (
    "Read from the statement — check each value before saving; nothing is stored "
    "until you confirm."
)


def _scan_line_date(raw: object) -> date | None:
    """A row's date, from the YYYY-MM-DD the prompt asks for or the MM/DD/YYYY a
    model sometimes copies off the page. A row printed without a year is NOT
    given one here — inventing a year is a guess, so the row is dropped."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    parts = text.replace("-", "/").split("/")
    if len(parts) != 3 or not all(part.strip().isdigit() for part in parts):
        return None
    month, day, year = (int(part) for part in parts)
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _scan_line_amount(raw: object) -> int | None:
    """Minor units, keeping the sign the model reported. "(45.00)" — the
    accounting way to write a negative — is honoured too."""
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("(") and text.endswith(")"):
            raw = "-" + text[1:-1]
    value = _scan_number(raw)
    return None if value is None else round(value * 100)


def parse_card_statement_scan_lines(data: object) -> list[CardStatementScanLine]:
    """#25: the transaction table off ONE page.

    Defensive in the same way as the summary parse: a row without a readable
    date, description or amount is SKIPPED rather than guessed at, and a table
    that is missing or shaped unexpectedly yields an empty list — never an
    error, so the summary still parses when the table does not.
    """
    if not isinstance(data, dict):
        return []
    rows = data.get("transactions")
    if not isinstance(rows, list):
        return []
    lines: list[CardStatementScanLine] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        occurred_on = _scan_line_date(row.get("date"))
        amount_minor = _scan_line_amount(row.get("amount"))
        description = row.get("description")
        description = description.strip() if isinstance(description, str) else ""
        if occurred_on is None or amount_minor is None or not description:
            continue
        lines.append(
            CardStatementScanLine(
                occurred_on=occurred_on,
                description=description[:300],
                amount_minor=amount_minor,
            )
        )
    return lines


def parse_card_statement_scan(text: str) -> CardStatementScanResult:
    """Defensive parse of the model's answer — candidates only, nothing saved."""
    import json as _json
    import re as _re

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = _re.sub(r"^```[a-z]*\s*|\s*```$", "", cleaned, flags=_re.IGNORECASE)
    try:
        data = _json.loads(cleaned)
        assert isinstance(data, dict)
    except (ValueError, AssertionError):
        return CardStatementScanResult(
            note="The statement could not be read — enter the values manually."
        )

    def money_minor(key: str) -> int | None:
        value = _scan_number(data.get(key))
        # A zero balance is legitimate (paid-off card), so only NEGATIVE is
        # rejected; a statement never states a negative amount due.
        return round(value * 100) if value is not None and value >= 0 else None

    def as_date(key: str) -> date | None:
        raw = data.get(key)
        if not isinstance(raw, str):
            return None
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            return None

    balance = money_minor("statement_balance")
    return CardStatementScanResult(
        lines=parse_card_statement_scan_lines(data),
        statement_balance_minor=balance,
        minimum_due_minor=money_minor("minimum_due"),
        due_date=as_date("due_date"),
        period_start=as_date("period_start"),
        period_end=as_date("period_end"),
        note=(
            _CARD_SCAN_NOTE
            if balance is not None
            else "No statement balance was found — enter the values manually."
        ),
    )


def parse_account_scan(text: str) -> AccountScanResult:
    """Defensive parse of ONE page's extraction — candidates only; nothing is
    saved until the user confirms. Multi-page results are merged by the caller."""
    import json as _json
    import re as _re

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = _re.sub(r"^```[a-z]*\s*|\s*```$", "", cleaned, flags=_re.IGNORECASE)
    try:
        data = _json.loads(cleaned)
        assert isinstance(data, dict)
    except (ValueError, AssertionError):
        return AccountScanResult(
            note="The photo could not be read as a statement — enter values manually."
        )

    def money_minor(key: str) -> int | None:
        value = _scan_number(data.get(key))
        return round(value * 100) if value is not None and value > 0 else None

    cash = money_minor("cash_balance")
    investment = money_minor("investment_value")
    # Back-compat: a model that still answers a single "balance" is treated as cash.
    if cash is None and investment is None:
        cash = money_minor("balance")
    raw_type = data.get("account_type")
    account_type = (
        _ACCOUNT_TYPE_ALIASES.get(str(raw_type).strip().lower())
        if raw_type is not None
        else None
    )
    name = data.get("account_name")
    return AccountScanResult(
        name=str(name).strip()[:120] if isinstance(name, str) and name.strip() else None,
        account_type=account_type,
        balance_minor=_account_scan_total(cash, investment),
        cash_balance_minor=cash,
        investment_value_minor=investment,
        statement_date=_parse_iso_or_us_date(data.get("statement_date")),
        note=_account_scan_note(cash, investment),
    )


def merge_account_scans(pages: list[AccountScanResult]) -> AccountScanResult:
    """Combine per-page reads: first non-null wins per field, and the balance is
    cash + investments — an HSA statement shows them on DIFFERENT pages, so no
    single page holds the true total (the $1,000-cash-next-to-$102k-invested
    lesson)."""
    name = next((p.name for p in pages if p.name), None)
    account_type = next((p.account_type for p in pages if p.account_type), None)
    statement_date = next((p.statement_date for p in pages if p.statement_date), None)
    cash = next((p.cash_balance_minor for p in pages if p.cash_balance_minor is not None), None)
    investment = next(
        (p.investment_value_minor for p in pages if p.investment_value_minor is not None), None
    )
    return AccountScanResult(
        name=name,
        account_type=account_type,
        balance_minor=_account_scan_total(cash, investment),
        cash_balance_minor=cash,
        investment_value_minor=investment,
        statement_date=statement_date,
        note=_account_scan_note(cash, investment),
    )


def _parse_iso_or_us_date(value: object) -> date | None:
    from datetime import datetime

    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _scan_number(value: object) -> float | None:
    """A number the model reported, whether as a JSON number or a string like
    "5.5%" or "$1,234.56". Unparseable values become None — never a guess."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "").replace(",", "").rstrip("%").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


_TXT_AMT = r"\$?([\d,]+\.\d{2})"
_TXT_DATE = r"(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"


def _first_match(text: str, patterns: list[str]) -> str | None:
    import re as _re

    for pat in patterns:
        m = _re.search(pat, text, _re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def parse_loan_statement_text(text: str) -> LoanScanResult | None:
    """Read a loan/card statement's fields straight from the PDF's own text.

    Labeled fields — including the interest rate, which a rasterized-image vision
    pass routinely misses in a detail table — parse far more reliably from text
    than from a photo. Returns None when it can't find a payment or a balance, so
    the caller can fall back to the vision model for a scanned (image) statement.
    """
    from family_cfo_api.import_processing import _parse_label_date

    payment = _scan_number(
        _first_match(
            text,
            [
                r"regular\s+monthly\s+payment\s+amount\s*" + _TXT_AMT,
                r"current\s+amount\s+due\s*" + _TXT_AMT,
                r"minimum\s+payment(?:\s+due)?\s*" + _TXT_AMT,
                r"\bamount\s+due\s*" + _TXT_AMT,
            ],
        )
    )
    balance = _scan_number(
        _first_match(
            text,
            [
                r"current\s+balance\s*" + _TXT_AMT,
                r"outstanding\s+principal\s+balance[^\n$]*" + _TXT_AMT,
                r"new\s+balance(?:\s+total)?\s*" + _TXT_AMT,
                r"payoff[^\n$]*" + _TXT_AMT,
            ],
        )
    )
    if payment is None and balance is None:
        return None
    rate = _scan_number(_first_match(text, [r"interest\s+rate\s*(?:is|:)?\s*([\d.]+)\s*%"]))
    due = _first_match(
        text,
        [
            r"(?:current\s+statement\s+|payment\s+)?due\s+date\s*[:\-]?\s*" + _TXT_DATE,
            r"payment\s+due\s*[:\-]?\s*" + _TXT_DATE,
        ],
    )
    return LoanScanResult(
        monthly_payment_minor=round(payment * 100) if payment and payment > 0 else None,
        balance_minor=round(balance * 100) if balance and balance > 0 else None,
        next_payment_due_date=_parse_label_date(due) if due else None,
        apr_percent=rate if rate is not None and 0 <= rate < 100 else None,
        note="Read from the statement text — confirm every value before saving.",
    )


def parse_loan_scan(text: str) -> LoanScanResult:
    """Defensive parse of the vision model's loan/lease extraction (candidates only).

    A lease statement rarely prints a "balance" — but it prints the monthly payment
    and a maturity date, so the remaining obligation is derived: months from the
    statement date to maturity × the monthly payment.
    """
    import json as _json
    import re as _re

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = _re.sub(r"^```[a-z]*\s*|\s*```$", "", cleaned, flags=_re.IGNORECASE)
    try:
        data = _json.loads(cleaned)
        assert isinstance(data, dict)
    except (ValueError, AssertionError):
        return LoanScanResult(
            note="The photo could not be read as a statement — enter values manually."
        )

    def money_minor(key: str) -> int | None:
        value = _scan_number(data.get(key))
        return round(value * 100) if value is not None and value > 0 else None

    def positive_int(key: str) -> int | None:
        value = _scan_number(data.get(key))
        return int(value) if value is not None and value > 0 else None

    monthly = money_minor("monthly_payment")
    payoff = money_minor("payoff_balance")
    remaining = positive_int("payments_remaining")
    is_lease = bool(data.get("is_lease"))
    statement_date = _parse_iso_or_us_date(data.get("statement_date"))
    payment_due_date = _parse_iso_or_us_date(data.get("payment_due_date"))
    maturity_date = _parse_iso_or_us_date(data.get("maturity_date"))

    base_note = (
        "Read by the on-box photo model — CONFIRM every value before saving. "
        "Nothing is stored until you save."
    )
    note = base_note

    # Payments left: the model's count if stated, else derive it from the maturity
    # date (the number of monthly payments from this statement through maturity).
    if remaining is None and monthly is not None and maturity_date is not None:
        anchor = statement_date or maturity_date
        months = (maturity_date.year - anchor.year) * 12 + (maturity_date.month - anchor.month)
        if months > 0:
            remaining = months

    # A lease has no amortizing balance; its remaining obligation is what's left to
    # pay = payments remaining × the monthly payment.
    balance = payoff
    if balance is None and remaining is not None and monthly is not None:
        balance = remaining * monthly
        until = f" until {maturity_date:%b %Y}" if maturity_date else ""
        note = (
            f"Balance estimated as {remaining} payments left{until} × the monthly "
            f"payment. " + base_note
        )

    apr = _scan_number(data.get("apr"))
    return LoanScanResult(
        name=str(data["lender"])[:120] if data.get("lender") else None,
        monthly_payment_minor=monthly,
        balance_minor=balance,
        payments_remaining=remaining,
        maturity_date=maturity_date,
        next_payment_due_date=payment_due_date,
        apr_percent=apr if apr is not None and 0 <= apr < 100 else None,
        is_lease=is_lease,
        note=note,
    )


def _min_payment(currency: str, minor: int | None) -> MoneySchema | None:
    return None if minor is None else MoneySchema(amount_minor=minor, currency=currency)


def _emergency_fund_fields(
    currency: str, percent: float | None, fixed_minor: int | None, balance_minor: int
) -> dict:
    """M36: designation + derived reservation for the Account schema."""
    reserved = repository.emergency_fund_reserved_minor(percent, fixed_minor, balance_minor)
    return {
        "emergency_fund_percent": percent,
        "emergency_fund_amount": _min_payment(currency, fixed_minor),
        "emergency_fund_reserved": (
            MoneySchema(amount_minor=reserved, currency=currency)
            if percent is not None or fixed_minor is not None
            else None
        ),
    }


def _account_schema(record: repository.AccountRecord, balance_minor: int) -> Account:
    return Account(
        id=record.id,
        name=record.name,
        type=record.account_type,
        balance=MoneySchema(amount_minor=balance_minor, currency=record.currency),
        annual_interest_rate=record.annual_interest_rate,
        minimum_payment=_min_payment(record.currency, record.minimum_payment_minor),
        maturity_date=record.maturity_date,
        next_payment_due_date=record.next_payment_due_date,
        **_emergency_fund_fields(
            record.currency,
            record.emergency_fund_percent,
            record.emergency_fund_minor,
            balance_minor,
        ),
        rsu_ready_to_sell=record.rsu_ready_to_sell,
    )


@router.get(
    "/accounts",
    operation_id="listAccounts",
    response_model=AccountListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List accounts",
)
async def list_accounts(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> AccountListResponse:
    balances = repository.list_account_balances(engine, session.household_id)
    connections = repository.account_connection_map(engine, session.household_id)
    # Prefer the real per-account institution (SimpleFIN's org, e.g. "Charles
    # Schwab") over the generic connection name ("SimpleFin (multiple banks)").
    institutions = repository.account_institution_map(engine, session.household_id)
    return AccountListResponse(
        accounts=[
            Account(
                id=balance.account_id,
                name=balance.name,
                type=balance.account_type,
                balance=MoneySchema(amount_minor=balance.balance_minor, currency=balance.currency),
                annual_interest_rate=balance.annual_interest_rate,
                minimum_payment=_min_payment(balance.currency, balance.minimum_payment_minor),
                maturity_date=balance.maturity_date,
                next_payment_due_date=balance.next_payment_due_date,
                institution=(
                    institutions.get(balance.account_id)
                    or ((info := connections.get(balance.account_id)) and info.institution)
                ),
                last_synced_at=(
                    (info2 := connections.get(balance.account_id)) and info2.last_synced_at
                ),
                **_emergency_fund_fields(
                    balance.currency,
                    balance.emergency_fund_percent,
                    balance.emergency_fund_minor,
                    balance.balance_minor,
                ),
                rsu_ready_to_sell=balance.rsu_ready_to_sell,
            )
            for balance in balances
        ]
    )


@router.post(
    "/accounts",
    operation_id="createAccount",
    response_model=Account,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Create an account",
)
async def create_account(
    payload: AccountCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Account:
    if payload.minimum_payment is not None and payload.minimum_payment.currency != payload.currency:
        raise HTTPException(
            status_code=400, detail=f"minimum_payment currency must be {payload.currency}"
        )
    record = repository.create_account(
        engine,
        household_id=session.household_id,
        name=payload.name,
        account_type=payload.type,
        currency=payload.currency,
        annual_interest_rate=payload.annual_interest_rate,
        minimum_payment_minor=(
            payload.minimum_payment.amount_minor if payload.minimum_payment is not None else None
        ),
        maturity_date=payload.maturity_date,
        next_payment_due_date=payload.next_payment_due_date,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "account.created",
        "account",
        record.id,
        f"Created account '{record.name}'",
        undo_token=undo_actions.created("account", record.id),
    )
    return _account_schema(record, balance_minor=0)


@router.patch(
    "/accounts/{account_id}",
    operation_id="updateAccount",
    response_model=Account,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
    },
    summary="Update an account",
)
async def update_account(
    account_id: str,
    payload: AccountUpdateRequest,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Account:
    existing = repository.get_account(engine, session.household_id, account_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if (
        payload.minimum_payment is not None
        and payload.minimum_payment.currency != existing.currency
    ):
        raise HTTPException(
            status_code=400, detail=f"minimum_payment currency must be {existing.currency}"
        )
    # M36: percent and fixed-amount designations are mutually exclusive.
    if payload.emergency_fund_percent is not None and payload.emergency_fund_amount is not None:
        raise HTTPException(
            status_code=400,
            detail="Set emergency_fund_percent or emergency_fund_amount, not both",
        )
    if payload.emergency_fund_amount is not None and (
        payload.emergency_fund_amount.currency != existing.currency
        or payload.emergency_fund_amount.amount_minor < 0
    ):
        raise HTTPException(
            status_code=400,
            detail=f"emergency_fund_amount must be non-negative {existing.currency}",
        )
    repository.update_account(
        engine,
        session.household_id,
        account_id,
        name=payload.name,
        account_type=payload.type,
        annual_interest_rate=payload.annual_interest_rate,
        minimum_payment_minor=(
            payload.minimum_payment.amount_minor if payload.minimum_payment is not None else None
        ),
        maturity_date=payload.maturity_date,
        next_payment_due_date=payload.next_payment_due_date,
        emergency_fund_percent=payload.emergency_fund_percent,
        emergency_fund_minor=(
            payload.emergency_fund_amount.amount_minor
            if payload.emergency_fund_amount is not None
            else None
        ),
        clear_emergency_fund=payload.clear_emergency_fund,
        rsu_ready_to_sell=payload.rsu_ready_to_sell,
    )
    record = repository.get_account(engine, session.household_id, account_id)
    assert record is not None
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "account.updated",
        "account",
        account_id,
        f"Updated account “{record.name}”",
        undo_token=undo_actions.account_updated(existing),
    )
    balance_minor = repository.get_latest_balance_minor(engine, account_id)
    return _account_schema(record, balance_minor)


@router.delete(
    "/accounts/{account_id}",
    operation_id="deleteAccount",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
        409: {"description": "Account is referenced by other records", "model": ErrorResponse},
    },
    summary="Delete an account",
)
async def delete_account(
    account_id: str,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    existing = repository.get_account(engine, session.household_id, account_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if repository.account_in_use(engine, account_id):
        raise HTTPException(
            status_code=409,
            detail="Account is referenced by transactions, bills, or imports; reassign or delete those first",
        )
    repository.delete_account(engine, session.household_id, account_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "account.deleted",
        "account",
        account_id,
        f"Deleted account “{existing.name}”",
        undo_token=undo_actions.account_deleted(existing),
    )
    return Response(status_code=204)


@router.post(
    "/accounts/{account_id}/balances",
    operation_id="recordAccountBalance",
    response_model=Account,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
    },
    summary="Record a new balance for an account",
)
async def record_account_balance(
    account_id: str,
    payload: AccountBalanceCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Account:
    record = repository.get_account(engine, session.household_id, account_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if payload.balance.currency != record.currency:
        raise HTTPException(status_code=400, detail=f"Balance currency must be {record.currency}")
    balance_id = repository.record_account_balance(
        engine, account_id, payload.balance.amount_minor
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "account.balance_recorded",
        "account",
        account_id,
        f"Recorded a new balance for “{record.name}”",
        undo_token=undo_actions.balance_recorded(balance_id),
    )
    return _account_schema(record, payload.balance.amount_minor)


@router.post(
    "/accounts/scan-statement",
    operation_id="scanLoanStatement",
    response_model=LoanScanResult,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        422: {"description": "Unreadable PDF", "model": ErrorResponse},
        503: {"description": "No vision model available", "model": ErrorResponse},
    },
    summary="Read a loan/lease statement photo or PDF into candidate values",
)
async def scan_loan_statement(
    payload: LoanScanRequest,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> LoanScanResult:
    import base64
    import binascii

    from family_cfo_ai_orchestrator import RuntimeMessage, RuntimeUnavailableError

    from family_cfo_api.ai_runtime_selection import select_vision_describer
    from family_cfo_api.api.income_analysis import pdf_page_pngs

    if payload.image_media_type == "application/pdf":
        try:
            pdf_bytes = base64.b64decode(payload.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid PDF upload") from exc
        # A text-based statement (most e-statements) parses far more reliably from
        # its own text than from a rasterized image — and it catches the interest
        # rate the vision model misses. Fall through to vision only for a scanned
        # (image-only) PDF where there is no extractable text.
        from family_cfo_ocr_worker import PdfTextExtractionAdapter

        pdf_text = PdfTextExtractionAdapter().extract(pdf_bytes, "application/pdf").text
        text_result = parse_loan_statement_text(pdf_text) if pdf_text else None
        if text_result is not None:
            return text_result
        data_urls = [
            "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            for png in pdf_page_pngs(pdf_bytes)
        ]
    else:
        data_urls = [f"data:{payload.image_media_type};base64,{payload.image_base64}"]

    describer, _source = select_vision_describer(engine, session.household_id)
    if describer is None:
        raise HTTPException(status_code=503, detail="No vision model is configured")
    try:
        result = None
        for data_url in data_urls:
            completion = describer.complete(
                [RuntimeMessage(role="user", content=_LOAN_PROMPT, image_data_url=data_url)],
                temperature=0.0,
                max_tokens=500,
                thinking=False,
            )
            result = parse_loan_scan(completion.text)
            if result.monthly_payment_minor is not None or result.balance_minor is not None:
                return result
        return result or LoanScanResult(note="Nothing readable was found — enter values manually.")
    except RuntimeUnavailableError as exc:
        raise HTTPException(status_code=503, detail="The vision model is unavailable") from exc
    finally:
        describer.close()


@router.post(
    "/accounts/scan",
    operation_id="scanAccountStatement",
    response_model=AccountScanResult,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        422: {"description": "Unreadable PDF", "model": ErrorResponse},
        503: {"description": "No vision model available", "model": ErrorResponse},
    },
    summary="Read an asset-account statement photo or PDF into add-account candidates",
)
async def scan_account_statement(
    payload: AccountScanRequest,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> AccountScanResult:
    """ADR 0057: paste/photograph an HSA/savings/brokerage statement and the
    add-account form is prefilled — name, type, current balance. Candidates
    only; the user confirms before anything is saved."""
    import base64
    import binascii

    from family_cfo_ai_orchestrator import RuntimeMessage, RuntimeUnavailableError

    from family_cfo_api.ai_runtime_selection import select_vision_describer
    from family_cfo_api.api.income_analysis import pdf_page_pngs

    if payload.image_media_type == "application/pdf":
        try:
            pdf_bytes = base64.b64decode(payload.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid PDF upload") from exc
        data_urls = [
            "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            # 6 pages, not the W2 default 4 — an HSA statement buries its
            # Investment Portfolio section behind fee tables and disclosures.
            for png in pdf_page_pngs(pdf_bytes, max_pages=6)
        ]
    else:
        data_urls = [f"data:{payload.image_media_type};base64,{payload.image_base64}"]

    describer, _source = select_vision_describer(engine, session.household_id)
    if describer is None:
        raise HTTPException(status_code=503, detail="No vision model is configured")
    try:
        # Read EVERY page and merge: an HSA statement shows the cash balance and
        # the invested value on different pages — stopping at the first readable
        # page would report $1,000 cash and miss $102k invested. Stop early only
        # once both components have been found.
        pages: list[AccountScanResult] = []
        for data_url in data_urls:
            completion = describer.complete(
                [RuntimeMessage(role="user", content=_ACCOUNT_PROMPT, image_data_url=data_url)],
                temperature=0.0,
                max_tokens=500,
                thinking=False,
            )
            pages.append(parse_account_scan(completion.text))
            merged = merge_account_scans(pages)
            if (
                merged.cash_balance_minor is not None
                and merged.investment_value_minor is not None
                and merged.name is not None
            ):
                return merged
        if pages:
            merged = merge_account_scans(pages)
            if merged.balance_minor is not None or merged.name is not None:
                return merged
        return AccountScanResult(
            note="Nothing readable was found — enter values manually."
        )
    except RuntimeUnavailableError as exc:
        raise HTTPException(status_code=503, detail="The vision model is unavailable") from exc
    finally:
        describer.close()


# --- #11: credit-card statements -------------------------------------------
# A card's synced balance is not what is due — it includes spending posted after
# the cycle closed. These endpoints record the STATEMENT figure and its real due
# date, which the Due Soon timeline and safe-to-spend then use in place of the
# running-balance estimate.


def _statement_schema(record, names: dict[str, str]) -> CardStatement:
    return CardStatement(
        id=record.id,
        account_id=record.account_id,
        account_name=names.get(record.account_id, "Card"),
        statement_balance=MoneySchema(
            amount_minor=record.statement_balance_minor, currency=record.currency
        ),
        minimum_due=(
            MoneySchema(amount_minor=record.minimum_due_minor, currency=record.currency)
            if record.minimum_due_minor is not None
            else None
        ),
        due_date=record.due_date,
        period_start=record.period_start,
        period_end=record.period_end,
        document_id=record.document_id,
        paid_at=record.paid_at,
    )


@router.get(
    "/accounts/card-statements",
    operation_id="listCardStatements",
    response_model=CardStatementListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Credit-card statements, newest cycle first (#11)",
)
async def list_card_statements(
    account_id: str | None = None,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> CardStatementListResponse:
    names = repository.account_name_map(engine, session.household_id)
    return CardStatementListResponse(
        statements=[
            _statement_schema(record, names)
            for record in repository.list_card_statements(
                engine, session.household_id, account_id=account_id
            )
        ]
    )


@router.post(
    "/accounts/card-statements",
    operation_id="recordCardStatement",
    response_model=CardStatement,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
        422: {"description": "Not a credit-card account", "model": ErrorResponse},
    },
    summary="Record the amount due for a card's cycle (#11)",
)
async def record_card_statement(
    payload: CardStatementCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> CardStatement:
    account = repository.get_account(engine, session.household_id, payload.account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if account.account_type != "credit_card":
        # Loans and leases model their obligation with a monthly payment; a
        # statement cycle is a credit-card concept.
        raise HTTPException(status_code=422, detail="Not a credit-card account")

    record = repository.upsert_card_statement(
        engine,
        session.household_id,
        account_id=payload.account_id,
        statement_balance_minor=payload.statement_balance.amount_minor,
        currency=payload.statement_balance.currency,
        due_date=payload.due_date,
        minimum_due_minor=(
            payload.minimum_due.amount_minor if payload.minimum_due else None
        ),
        period_start=payload.period_start,
        period_end=payload.period_end,
        document_id=payload.document_id,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "card_statement.recorded",
        "card_statement",
        record.id,
        f"Statement for {account.name} due {payload.due_date.isoformat()}",
        undo_token=undo_actions.created("card_statement", record.id),
    )
    names = repository.account_name_map(engine, session.household_id)
    return _statement_schema(record, names)


@router.post(
    "/accounts/card-statements/{statement_id}/paid",
    operation_id="markCardStatementPaid",
    response_model=CardStatement,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Statement not found", "model": ErrorResponse},
    },
    summary="Mark a card's cycle paid, or clear the mark (#11)",
)
async def mark_card_statement_paid(
    statement_id: str,
    payload: CardStatementPaidRequest,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> CardStatement:
    before = repository.get_card_statement(engine, session.household_id, statement_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Statement not found")
    repository.set_card_statement_paid(
        engine, session.household_id, statement_id, payload.paid_at
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "card_statement.paid" if payload.paid_at else "card_statement.unpaid",
        "card_statement",
        statement_id,
        ("Marked a card statement paid" if payload.paid_at
         else "Cleared a card statement's paid mark"),
        undo_token=undo_actions.card_statement_paid_changed(before),
    )
    record = repository.get_card_statement(engine, session.household_id, statement_id)
    names = repository.account_name_map(engine, session.household_id)
    return _statement_schema(record, names)


@router.delete(
    "/accounts/card-statements/{statement_id}",
    operation_id="deleteCardStatement",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Statement not found", "model": ErrorResponse},
    },
    summary="Remove a recorded card statement (#11)",
)
async def delete_card_statement(
    statement_id: str,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    record = repository.get_card_statement(engine, session.household_id, statement_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Statement not found")
    repository.delete_card_statement(engine, session.household_id, statement_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "card_statement.deleted",
        "card_statement",
        statement_id,
        "Removed a card statement",
        undo_token=undo_actions.card_statement_deleted(record),
    )
    return Response(status_code=204)


@router.post(
    "/accounts/card-statements/scan",
    operation_id="scanCardStatement",
    response_model=CardStatementScanResult,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        422: {"description": "Unreadable PDF", "model": ErrorResponse},
        503: {"description": "No vision model available", "model": ErrorResponse},
    },
    summary="Read a credit-card statement into candidate values (#11)",
)
async def scan_card_statement(
    payload: CardStatementScanRequest,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> CardStatementScanResult:
    import base64
    import binascii

    from family_cfo_ai_orchestrator import RuntimeMessage, RuntimeUnavailableError

    from family_cfo_api.ai_runtime_selection import select_vision_describer
    from family_cfo_api.api.income_analysis import pdf_page_pngs

    if payload.image_media_type == "application/pdf":
        try:
            pdf_bytes = base64.b64decode(payload.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid PDF upload") from exc
        # The summary box sits on page 1, but #25 also wants the TRANSACTION
        # TABLE, which runs over several pages on a busy card — so read deeper.
        # The cap keeps a 40-page statement (disclosures, rewards inserts) from
        # turning into 40 vision calls.
        data_urls = [
            "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            for png in pdf_page_pngs(pdf_bytes, max_pages=10)
        ]
    else:
        data_urls = [f"data:{payload.image_media_type};base64,{payload.image_base64}"]

    describer, _source = select_vision_describer(engine, session.household_id)
    if describer is None:
        raise HTTPException(status_code=503, detail="No vision model is configured")
    try:
        summary: CardStatementScanResult | None = None
        last: CardStatementScanResult | None = None
        lines: list[CardStatementScanLine] = []
        empty_pages = 0
        for data_url in data_urls:
            completion = describer.complete(
                [
                    RuntimeMessage(
                        role="user", content=_CARD_STATEMENT_PROMPT, image_data_url=data_url
                    )
                ],
                temperature=0.0,
                # A page of the transaction table is far more JSON than the
                # summary box — too small a budget truncates mid-row and the
                # whole page parses as nothing.
                max_tokens=4000,
                thinking=False,
            )
            page = parse_card_statement_scan(completion.text)
            last = page
            # #25: the table spans pages, so every page contributes rows. Rows
            # are NOT de-duplicated — two identical coffees on one day are two
            # real charges, and dropping one would invent a missing charge.
            lines.extend(page.lines)
            # The summary box sits together, so the first page carrying the
            # balance carries the rest; later pages only add table rows.
            if summary is None and page.statement_balance_minor is not None:
                summary = page

            # Statements end in pages of disclosures and legal text that will
            # never yield a row. Once the summary is in hand, two consecutive
            # empty pages mean the table is behind us — stop paying for vision
            # calls that cannot contribute (a long statement otherwise costs 10
            # calls, most of them on fine print).
            empty_pages = empty_pages + 1 if not page.lines else 0
            if summary is not None and empty_pages >= 2:
                break
        result = summary or last or CardStatementScanResult(
            note="Nothing readable was found — enter the values manually."
        )
        return result.model_copy(update={"lines": lines})
    except RuntimeUnavailableError as exc:
        raise HTTPException(status_code=503, detail="The vision model is unavailable") from exc
    finally:
        describer.close()


# --- #25: statement reconciliation -----------------------------------------
# A statement balance says what is owed; its LINE ITEMS say whether the synced
# ledger is complete. A line with no matching transaction is a charge the bank
# feed never delivered — the thing worth surfacing.


def _period_label(statement) -> str:
    """"August 2026" — how a person refers to a statement."""
    anchor = statement.period_end or statement.period_start or statement.due_date
    return anchor.strftime("%B %Y")


def _reconciliation_payload(engine, household_id: str, statement) -> StatementReconciliation:
    from family_cfo_api import statement_reconciliation

    result = statement_reconciliation.reconcile_statement(
        engine, household_id, statement.id
    )
    stored = repository.list_statement_lines(engine, household_id, statement.id)
    names = repository.account_name_map(engine, household_id)

    by_id = {row.id: row for row in stored}
    unaccounted: list[UnaccountedTransaction] = []
    if result.unmatched_transaction_ids:
        wanted = set(result.unmatched_transaction_ids)
        for txn in repository.list_transactions(engine, household_id, limit=100_000):
            if txn.id in wanted:
                unaccounted.append(
                    UnaccountedTransaction(
                        transaction_id=txn.id,
                        occurred_at=txn.occurred_at,
                        merchant=txn.merchant or txn.description or "",
                        amount=MoneySchema(
                            amount_minor=txn.amount_minor, currency=txn.currency
                        ),
                    )
                )
    return StatementReconciliation(
        statement_id=statement.id,
        account_name=names.get(statement.account_id, "Card"),
        period_label=_period_label(statement),
        lines=[
            StatementLineSchema(
                id=row.id,
                occurred_on=row.occurred_on,
                description=row.description,
                amount=MoneySchema(amount_minor=row.amount_minor, currency=row.currency),
                matched_transaction_id=row.matched_transaction_id,
                match_kind=row.match_kind,
            )
            for row in by_id.values()
        ],
        unaccounted=sorted(unaccounted, key=lambda u: u.occurred_at),
        matched_count=result.matched_count,
        missing_from_sync_count=result.missing_from_sync_count,
        not_on_statement_count=result.not_on_statement_count,
        amount_differs_count=result.amount_differs_count,
    )


@router.put(
    "/accounts/card-statements/{statement_id}/lines",
    operation_id="replaceStatementLines",
    response_model=StatementReconciliation,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Statement not found", "model": ErrorResponse},
    },
    summary="Store a statement's line items and reconcile them (#25)",
)
async def replace_statement_lines(
    statement_id: str,
    payload: StatementLinesReplaceRequest,
    session: repository.SessionContext = Depends(require_right(rights.ACCOUNTS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> StatementReconciliation:
    statement = repository.get_card_statement(engine, session.household_id, statement_id)
    if statement is None:
        raise HTTPException(status_code=404, detail="Statement not found")

    repository.replace_statement_lines(
        engine,
        session.household_id,
        statement_id,
        [
            {
                "occurred_on": line.occurred_on,
                "description": line.description,
                "amount_minor": line.amount.amount_minor,
                "currency": line.amount.currency,
            }
            for line in payload.lines
        ],
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "statement_lines.recorded",
        "card_statement",
        statement_id,
        f"Recorded {len(payload.lines)} statement line(s)",
        undo_token=None,
    )
    return _reconciliation_payload(engine, session.household_id, statement)


@router.get(
    "/accounts/card-statements/{statement_id}/reconciliation",
    operation_id="getStatementReconciliation",
    response_model=StatementReconciliation,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Statement not found", "model": ErrorResponse},
    },
    summary="What the statement accounts for, and what it doesn't (#25)",
)
async def get_statement_reconciliation(
    statement_id: str,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> StatementReconciliation:
    """Re-runs matching on every read, so a sync that fills a gap turns an
    unmatched line into a matched one without anyone re-uploading."""
    statement = repository.get_card_statement(engine, session.household_id, statement_id)
    if statement is None:
        raise HTTPException(status_code=404, detail="Statement not found")
    return _reconciliation_payload(engine, session.household_id, statement)
