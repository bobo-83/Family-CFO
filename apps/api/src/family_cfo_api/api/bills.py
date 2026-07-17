from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, bill_detection, finance_service, repository, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    AccountObligation,
    Bill,
    BillCreateRequest,
    BillListResponse,
    BillSuggestion,
    BillSuggestionDismissRequest,
    BillSuggestionListResponse,
    BillUpdateSuggestion,
    BillUpdateRequest,
    ErrorResponse,
    PaymentTimelineItem,
    PaymentTimelineResponse,
    TimelinePaidWith,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Bills"])


def _to_schema(
    record: repository.RecurringRecord,
    category_names: dict[str, str] | None = None,
    transactions_categorized: int | None = None,
) -> Bill:
    names = category_names or {}
    return Bill(
        id=record.id,
        name=record.name,
        amount=MoneySchema(amount_minor=record.amount_minor, currency=record.currency),
        frequency=record.frequency,
        next_due_date=record.next_due_date,
        category_id=record.category_id,
        category_name=record.category_id and names.get(record.category_id),
        transactions_categorized=transactions_categorized,
    )


def _category_names(engine: Engine, household_id: str) -> dict[str, str]:
    return {c.id: c.name for c in repository.list_categories(engine, household_id)}


def _propagate_bill_category(
    engine: Engine, household_id: str, bill_name: str, category_id: str
) -> int:
    """M96 rule (minimize duplicate input): filing a bill under a category also
    files its still-UNCATEGORIZED matching transactions under the same category —
    match on the same normalized merchant the bill was detected from. Already-
    categorized transactions are left alone; the user's explicit choices win.
    Returns how many transactions were categorized, so the client can say so."""
    key = bill_detection.normalize_merchant(bill_name)
    if not key:
        return 0
    matched = [
        txn.id
        for txn in repository.list_transactions(engine, household_id, limit=10_000)
        if txn.category_id is None
        and bill_detection.normalize_merchant(txn.merchant or txn.description) == key
    ]
    return repository.set_transactions_category(engine, household_id, matched, category_id)


@router.get(
    "/bills",
    operation_id="listBills",
    response_model=BillListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List recurring bills for the household",
)
async def list_bills(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> BillListResponse:
    records = repository.list_bills(engine, session.household_id)
    names = _category_names(engine, session.household_id)
    household = repository.get_household(engine, session.household_id)
    currency = household.base_currency if household else "USD"
    obligations = [
        AccountObligation(
            account_id=obligation.account_id,
            name=obligation.name,
            amount=MoneySchema(amount_minor=obligation.amount_minor, currency=obligation.currency),
            kind=obligation.kind,
            note=obligation.note,
            reserved=obligation.reserved,
        )
        for obligation in finance_service.recurring_liability_obligations(
            engine, session.household_id, currency
        )
    ]
    return BillListResponse(
        bills=[_to_schema(record, names) for record in records],
        account_obligations=obligations,
    )


@router.get(
    "/bills/timeline",
    operation_id="getPaymentTimeline",
    response_model=PaymentTimelineResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Everything that needs paying, as one time-ordered list",
)
async def get_payment_timeline(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> PaymentTimelineResponse:
    """M111 (ADR 0024): the Bills tab's primary view. Bills, credit-card payments,
    and loan/lease payments in one list organized by time — overdue, due soon,
    upcoming, paid this cycle — with a cash-versus-due headline. Paid rows carry
    the actual matched transaction so the checkmark is verifiable."""
    household = repository.get_household(engine, session.household_id)
    currency = household.base_currency if household else "USD"
    today = date.today()
    timeline = finance_service.payment_timeline(
        engine, session.household_id, currency, today=today
    )
    return PaymentTimelineResponse(
        items=[
            PaymentTimelineItem(
                id=item.id,
                kind=item.kind,
                name=item.name,
                amount=MoneySchema(amount_minor=item.amount_minor, currency=item.currency),
                due_date=item.due_date,
                days_until=(item.due_date - today).days if item.due_date else None,
                status=item.status,
                paid_with=(
                    TimelinePaidWith(
                        transaction_id=item.paid.transaction_id,
                        occurred_at=item.paid.occurred_at,
                        amount=MoneySchema(
                            amount_minor=item.paid.amount_minor, currency=item.currency
                        ),
                        label=item.paid.label,
                    )
                    if item.paid
                    else None
                ),
            )
            for item in timeline.items
        ],
        due_total=MoneySchema(amount_minor=timeline.due_total_minor, currency=currency),
        liquid_balance=MoneySchema(amount_minor=timeline.liquid_minor, currency=currency),
        covered=timeline.covered,
        window_days=timeline.window_days,
    )


@router.get(
    "/bills/suggestions",
    operation_id="listBillSuggestions",
    response_model=BillSuggestionListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Suggest bills detected from recurring account transactions",
)
async def list_bill_suggestions(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> BillSuggestionListResponse:
    """M58: deterministic recurring-charge detection over checking/credit-card outflows.

    Candidates matching an existing bill's name or a stored dismissal are
    excluded, so confirming (POST /bills) or dismissing removes them.
    """
    since = date.today() - timedelta(days=bill_detection.LOOKBACK_DAYS)
    rows = repository.list_bill_detection_transactions(engine, session.household_id, since=since)
    candidates = bill_detection.detect_bill_candidates(
        [
            bill_detection.DetectionTransaction(
                occurred_at=occurred_at,
                amount_minor=amount_minor,
                currency=currency,
                merchant=merchant,
                description=description,
            )
            for occurred_at, amount_minor, currency, merchant, description in rows
        ]
    )
    bills_by_key: dict[str, repository.RecurringRecord] = {}
    for bill in repository.list_bills(engine, session.household_id):
        bills_by_key.setdefault(bill_detection.normalize_merchant(bill.name), bill)
    dismissed = repository.list_bill_suggestion_dismissals(engine, session.household_id)

    # A recurring payment already tracked as a loan/lease account has its monthly
    # payment reserved via minimum-debt-payments (ADR 0020); don't also suggest it
    # as a bill, which would nag the user to double-model the same obligation
    # (ADR 0020 "each commitment reserved once"). Match a monthly candidate whose
    # amount equals a liability account's monthly payment (fixed loan/lease
    # payments are exact; allow a small tolerance for rounding).
    liability_amounts: dict[str, list[int]] = {}
    for currency in {candidate.currency for candidate in candidates}:
        liability_amounts[currency] = [
            obligation.amount_minor
            for obligation in finance_service.recurring_liability_obligations(
                engine, session.household_id, currency
            )
        ]

    def _tracked_as_loan(candidate: bill_detection.BillCandidate) -> bool:
        if candidate.frequency != "monthly":
            return False
        return any(
            abs(candidate.amount_minor - amount)
            <= max(200, int(amount * bill_detection.DRIFT_TOLERANCE))
            for amount in liability_amounts.get(candidate.currency, [])
        )

    suggestions: list[BillSuggestion] = []
    updates: list[BillUpdateSuggestion] = []
    for candidate in candidates:
        existing = bills_by_key.get(candidate.merchant_key)
        if existing is None:
            if candidate.merchant_key not in dismissed and not _tracked_as_loan(candidate):
                suggestions.append(
                    BillSuggestion(
                        merchant_key=candidate.merchant_key,
                        name=candidate.name,
                        amount=MoneySchema(
                            amount_minor=candidate.amount_minor, currency=candidate.currency
                        ),
                        frequency=candidate.frequency,
                        next_due_date=candidate.next_due_date,
                        occurrences=candidate.occurrences,
                        last_seen=candidate.last_seen,
                    )
                )
            continue
        # M59 drift: an existing bill whose live charge pattern has moved.
        # Updates always need user confirmation — nothing changes silently.
        if existing.currency != candidate.currency:
            continue
        amount_drift = abs(candidate.amount_minor - existing.amount_minor) > (
            existing.amount_minor * bill_detection.DRIFT_TOLERANCE
        )
        cadence_drift = candidate.frequency != existing.frequency
        if not amount_drift and not cadence_drift:
            continue
        # Dismissals are keyed by the suggested amount: a dismissed price
        # re-prompts only when the detected price changes again.
        dismiss_key = f"{candidate.merchant_key}@{candidate.amount_minor}"
        if dismiss_key in dismissed:
            continue
        updates.append(
            BillUpdateSuggestion(
                bill_id=existing.id,
                name=existing.name,
                dismiss_key=dismiss_key,
                current_amount=MoneySchema(
                    amount_minor=existing.amount_minor, currency=existing.currency
                ),
                suggested_amount=MoneySchema(
                    amount_minor=candidate.amount_minor, currency=candidate.currency
                ),
                frequency=candidate.frequency,
                next_due_date=candidate.next_due_date,
                occurrences=candidate.occurrences,
                last_seen=candidate.last_seen,
            )
        )
    return BillSuggestionListResponse(suggestions=suggestions, updates=updates)


@router.post(
    "/bills/suggestions/dismissals",
    operation_id="dismissBillSuggestion",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Dismiss a suggested bill (not a bill)",
)
async def dismiss_bill_suggestion(
    payload: BillSuggestionDismissRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    repository.add_bill_suggestion_dismissal(engine, session.household_id, payload.merchant_key)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "bill_suggestion.dismissed",
        "bill_suggestion",
        payload.merchant_key,
        f"Dismissed suggested bill '{payload.merchant_key}'",
        undo_token=undo_actions.suggestion_dismissed(payload.merchant_key),
    )
    return Response(status_code=204)


@router.post(
    "/bills",
    operation_id="createBill",
    response_model=Bill,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Create a recurring bill",
)
async def create_bill(
    payload: BillCreateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Bill:
    if payload.account_id is not None:
        if repository.get_account(engine, session.household_id, payload.account_id) is None:
            raise HTTPException(status_code=404, detail="Account not found")
    if payload.category_id is not None:
        if repository.get_category(engine, session.household_id, payload.category_id) is None:
            raise HTTPException(status_code=404, detail="Category not found")
    record = repository.create_bill(
        engine,
        household_id=session.household_id,
        name=payload.name,
        amount_minor=payload.amount.amount_minor,
        currency=payload.amount.currency,
        frequency=payload.frequency,
        account_id=payload.account_id,
        next_due_date=payload.next_due_date,
        category_id=payload.category_id,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "bill.created",
        "bill",
        record.id,
        f"Created bill '{record.name}'",
        undo_token=undo_actions.created("bill", record.id),
    )
    propagated = (
        _propagate_bill_category(engine, session.household_id, record.name, payload.category_id)
        if payload.category_id is not None
        else None
    )
    return _to_schema(
        record, _category_names(engine, session.household_id), propagated
    )


@router.patch(
    "/bills/{bill_id}",
    operation_id="updateBill",
    response_model=Bill,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Bill not found", "model": ErrorResponse},
    },
    summary="Update a recurring bill",
)
async def update_bill(
    bill_id: str,
    payload: BillUpdateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Bill:
    before = repository.get_bill(engine, session.household_id, bill_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    amount_minor = payload.amount.amount_minor if payload.amount is not None else None
    currency = payload.amount.currency if payload.amount is not None else None
    # Only touch the category if the client actually sent the field (set OR clear);
    # a value must name a real category.
    category_changed = "category_id" in payload.model_fields_set
    if category_changed and payload.category_id is not None:
        if repository.get_category(engine, session.household_id, payload.category_id) is None:
            raise HTTPException(status_code=404, detail="Category not found")
    repository.update_bill(
        engine,
        session.household_id,
        bill_id,
        name=payload.name,
        amount_minor=amount_minor,
        currency=currency,
        frequency=payload.frequency,
        next_due_date=payload.next_due_date,
        category_id=payload.category_id if category_changed else repository._UNSET,
    )
    record = repository.get_bill(engine, session.household_id, bill_id)
    assert record is not None
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "bill.updated",
        "bill",
        bill_id,
        f"Updated bill “{record.name}”",
        undo_token=undo_actions.bill_updated(before),
    )
    # Propagate when this update SET a category (not on a clear).
    propagated = (
        _propagate_bill_category(engine, session.household_id, record.name, payload.category_id)
        if category_changed and payload.category_id is not None
        else None
    )
    return _to_schema(
        record, _category_names(engine, session.household_id), propagated
    )


@router.delete(
    "/bills/{bill_id}",
    operation_id="deleteBill",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Bill not found", "model": ErrorResponse},
    },
    summary="Delete a recurring bill",
)
async def delete_bill(
    bill_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    existing = repository.get_bill(engine, session.household_id, bill_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    repository.delete_bill(engine, session.household_id, bill_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "bill.deleted",
        "bill",
        bill_id,
        f"Deleted bill “{existing.name}”",
        undo_token=undo_actions.bill_deleted(existing),
    )
    return Response(status_code=204)
