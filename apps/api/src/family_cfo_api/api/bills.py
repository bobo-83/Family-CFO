from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, bill_detection, repository
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    Bill,
    BillCreateRequest,
    BillListResponse,
    BillSuggestion,
    BillSuggestionDismissRequest,
    BillSuggestionListResponse,
    BillUpdateSuggestion,
    BillUpdateRequest,
    ErrorResponse,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Bills"])


def _to_schema(
    record: repository.RecurringRecord, category_names: dict[str, str] | None = None
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
    )


def _category_names(engine: Engine, household_id: str) -> dict[str, str]:
    return {c.id: c.name for c in repository.list_categories(engine, household_id)}


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
    return BillListResponse(bills=[_to_schema(record, names) for record in records])


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

    suggestions: list[BillSuggestion] = []
    updates: list[BillUpdateSuggestion] = []
    for candidate in candidates:
        existing = bills_by_key.get(candidate.merchant_key)
        if existing is None:
            if candidate.merchant_key not in dismissed:
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
    )
    return _to_schema(record, _category_names(engine, session.household_id))


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
    if repository.get_bill(engine, session.household_id, bill_id) is None:
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
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "bill.updated",
        "bill",
        bill_id,
        "Updated a bill",
    )
    record = repository.get_bill(engine, session.household_id, bill_id)
    assert record is not None
    return _to_schema(record, _category_names(engine, session.household_id))


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
    if repository.get_bill(engine, session.household_id, bill_id) is None:
        raise HTTPException(status_code=404, detail="Bill not found")
    repository.delete_bill(engine, session.household_id, bill_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "bill.deleted",
        "bill",
        bill_id,
        "Deleted a bill",
    )
    return Response(status_code=204)
