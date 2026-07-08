from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    ErrorResponse,
    IncomeCreateRequest,
    IncomeListResponse,
    IncomeSource,
    IncomeUpdateRequest,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Income"])


def _to_schema(record: repository.RecurringRecord) -> IncomeSource:
    return IncomeSource(
        id=record.id,
        name=record.name,
        amount=MoneySchema(amount_minor=record.amount_minor, currency=record.currency),
        frequency=record.frequency,
    )


@router.get(
    "/income",
    operation_id="listIncomeSources",
    response_model=IncomeListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List income sources for the household",
)
async def list_income_sources(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> IncomeListResponse:
    records = repository.list_income_sources(engine, session.household_id)
    return IncomeListResponse(income=[_to_schema(record) for record in records])


@router.post(
    "/income",
    operation_id="createIncomeSource",
    response_model=IncomeSource,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Create an income source",
)
async def create_income_source(
    payload: IncomeCreateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> IncomeSource:
    record = repository.create_income_source(
        engine,
        household_id=session.household_id,
        name=payload.name,
        amount_minor=payload.amount.amount_minor,
        currency=payload.amount.currency,
        frequency=payload.frequency,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "income.created",
        "income",
        record.id,
        f"Created income source '{record.name}'",
    )
    return _to_schema(record)


@router.patch(
    "/income/{income_id}",
    operation_id="updateIncomeSource",
    response_model=IncomeSource,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Income source not found", "model": ErrorResponse},
    },
    summary="Update an income source",
)
async def update_income_source(
    income_id: str,
    payload: IncomeUpdateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> IncomeSource:
    if repository.get_income_source(engine, session.household_id, income_id) is None:
        raise HTTPException(status_code=404, detail="Income source not found")
    amount_minor = payload.amount.amount_minor if payload.amount is not None else None
    currency = payload.amount.currency if payload.amount is not None else None
    repository.update_income_source(
        engine,
        session.household_id,
        income_id,
        name=payload.name,
        amount_minor=amount_minor,
        currency=currency,
        frequency=payload.frequency,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "income.updated",
        "income",
        income_id,
        "Updated an income source",
    )
    record = repository.get_income_source(engine, session.household_id, income_id)
    assert record is not None
    return _to_schema(record)


@router.delete(
    "/income/{income_id}",
    operation_id="deleteIncomeSource",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Income source not found", "model": ErrorResponse},
    },
    summary="Delete an income source",
)
async def delete_income_source(
    income_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    if repository.get_income_source(engine, session.household_id, income_id) is None:
        raise HTTPException(status_code=404, detail="Income source not found")
    repository.delete_income_source(engine, session.household_id, income_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "income.deleted",
        "income",
        income_id,
        "Deleted an income source",
    )
    return Response(status_code=204)
