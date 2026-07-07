from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.deps import get_current_session, get_engine
from family_cfo_api.schemas import ErrorResponse
from family_cfo_api.schemas import Money as MoneySchema
from family_cfo_api.schemas import Transaction, TransactionListResponse

router = APIRouter(tags=["Transactions"])


@router.get(
    "/transactions",
    operation_id="listTransactions",
    response_model=TransactionListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List transactions for the household",
)
async def list_transactions(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> TransactionListResponse:
    records = repository.list_transactions(engine, session.household_id)
    return TransactionListResponse(
        transactions=[
            Transaction(
                id=record.id,
                account_id=record.account_id,
                occurred_at=record.occurred_at,
                amount=MoneySchema(amount_minor=record.amount_minor, currency=record.currency),
                merchant=record.merchant,
                category=record.category,
                description=record.description,
            )
            for record in records
        ]
    )
