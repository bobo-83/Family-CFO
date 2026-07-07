from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, repository
from family_cfo_api.deps import get_current_session, get_engine
from family_cfo_api.schemas import ErrorResponse, HouseholdContext
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Household"])


@router.get(
    "/household",
    operation_id="getHouseholdContext",
    response_model=HouseholdContext,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Get household financial context summary",
)
async def get_household_context(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> HouseholdContext:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")

    net_worth_result = finance_service.compute_net_worth(engine, household.id, household.base_currency)
    emergency_fund_result = finance_service.compute_emergency_fund(
        engine, household.id, household.base_currency
    )

    return HouseholdContext(
        household_id=household.id,
        display_name=household.display_name,
        currency=household.base_currency,
        net_worth=MoneySchema(**net_worth_result.outputs["net_worth"].to_dict()),
        emergency_fund_months=emergency_fund_result.outputs["emergency_fund_months"],
    )
