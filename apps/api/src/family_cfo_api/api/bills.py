from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    Bill,
    BillCreateRequest,
    BillListResponse,
    BillUpdateRequest,
    ErrorResponse,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Bills"])


def _to_schema(record: repository.RecurringRecord) -> Bill:
    return Bill(
        id=record.id,
        name=record.name,
        amount=MoneySchema(amount_minor=record.amount_minor, currency=record.currency),
        frequency=record.frequency,
        next_due_date=record.next_due_date,
    )


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
    return BillListResponse(bills=[_to_schema(record) for record in records])


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
    record = repository.create_bill(
        engine,
        household_id=session.household_id,
        name=payload.name,
        amount_minor=payload.amount.amount_minor,
        currency=payload.amount.currency,
        frequency=payload.frequency,
        account_id=payload.account_id,
        next_due_date=payload.next_due_date,
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
    return _to_schema(record)


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
    repository.update_bill(
        engine,
        session.household_id,
        bill_id,
        name=payload.name,
        amount_minor=amount_minor,
        currency=currency,
        frequency=payload.frequency,
        next_due_date=payload.next_due_date,
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
    return _to_schema(record)


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
