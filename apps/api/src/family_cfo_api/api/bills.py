from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.deps import get_current_session, get_engine
from family_cfo_api.schemas import Bill, BillListResponse, ErrorResponse
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Bills"])


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
    return BillListResponse(
        bills=[
            Bill(
                id=record.id,
                name=record.name,
                amount=MoneySchema(amount_minor=record.amount_minor, currency=record.currency),
                frequency=record.frequency,
            )
            for record in records
        ]
    )
