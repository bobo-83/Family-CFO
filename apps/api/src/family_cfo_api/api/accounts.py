from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    Account,
    AccountBalanceCreateRequest,
    AccountCreateRequest,
    AccountListResponse,
    AccountUpdateRequest,
    ErrorResponse,
    LoanScanRequest,
    LoanScanResult,
)
from family_cfo_api.schemas import Money as MoneySchema

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
        value = data.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(round(float(value) * 100))
        return None

    def positive_int(key: str) -> int | None:
        value = data.get(key)
        if isinstance(value, int) and value > 0:
            return value
        return None

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

    apr = data.get("apr")
    return LoanScanResult(
        name=str(data["lender"])[:120] if data.get("lender") else None,
        monthly_payment_minor=monthly,
        balance_minor=balance,
        payments_remaining=remaining,
        maturity_date=maturity_date,
        next_payment_due_date=payment_due_date,
        apr_percent=float(apr) if isinstance(apr, (int, float)) and 0 <= apr < 100 else None,
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
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
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
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
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
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
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
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
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
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
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
                max_tokens=200,
            )
            result = parse_loan_scan(completion.text)
            if result.monthly_payment_minor is not None or result.balance_minor is not None:
                return result
        return result or LoanScanResult(note="Nothing readable was found — enter values manually.")
    except RuntimeUnavailableError as exc:
        raise HTTPException(status_code=503, detail="The vision model is unavailable") from exc
    finally:
        describer.close()
