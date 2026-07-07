from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.deps import get_current_session, get_engine
from family_cfo_api.schemas import ErrorResponse, IncomeListResponse, IncomeSource
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Income"])


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
    return IncomeListResponse(
        income=[
            IncomeSource(
                id=record.id,
                name=record.name,
                amount=MoneySchema(amount_minor=record.amount_minor, currency=record.currency),
                frequency=record.frequency,
            )
            for record in records
        ]
    )
