from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.deps import get_current_session, get_engine
from family_cfo_api.schemas import Account, AccountListResponse, ErrorResponse
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Accounts"])


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
    return AccountListResponse(
        accounts=[
            Account(
                id=balance.account_id,
                name=balance.name,
                type=balance.account_type,
                balance=MoneySchema(amount_minor=balance.balance_minor, currency=balance.currency),
            )
            for balance in balances
        ]
    )
