from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, repository
from family_cfo_api.deps import get_current_session, get_engine
from family_cfo_api.schemas import (
    AssetCategoryTotal,
    EmergencyFundSummary,
    ErrorResponse,
    GoalProgress,
    HouseholdContext,
    MerchantSpend,
    MonthlyCashFlow,
    NetWorthPoint,
    SpendingInsights,
    UpcomingBill,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Household"])


def _emergency_fund_summary(
    months: float | None, inputs: finance_service.EmergencyFundInputs, currency: str
) -> EmergencyFundSummary:
    """M38: coverage vs the standard 3–6 month guidance, with the gap in dollars."""
    recommended = finance_service.EMERGENCY_FUND_TARGET_RECOMMENDED_MONTHS
    fund_minor = inputs.fund.amount_minor
    bills_minor = inputs.monthly_bills.amount_minor

    gap: MoneySchema | None = None
    if months is None:
        status = "no_bills"
    else:
        gap_minor = max(0, round(recommended * bills_minor) - fund_minor)
        gap = MoneySchema(amount_minor=gap_minor, currency=currency)
        if fund_minor <= 0:
            status = "no_fund"
        elif months < finance_service.EMERGENCY_FUND_TARGET_MIN_MONTHS:
            status = "getting_started"
        elif months < recommended:
            status = "on_track"
        else:
            status = "fully_funded"

    return EmergencyFundSummary(
        months=months,
        reserved=MoneySchema(amount_minor=fund_minor, currency=currency),
        using_designations=inputs.using_designations,
        monthly_expenses=MoneySchema(amount_minor=bills_minor, currency=currency),
        target_months_min=finance_service.EMERGENCY_FUND_TARGET_MIN_MONTHS,
        target_months_recommended=recommended,
        gap_to_recommended=gap,
        status=status,
    )


def _asset_and_debt_summary(
    engine: Engine, household_id: str, currency: str
) -> tuple[list[AssetCategoryTotal], MoneySchema]:
    totals: dict[str, int] = {}
    debt_minor = 0
    for balance in repository.list_account_balances(engine, household_id):
        if balance.currency != currency:
            continue
        if balance.balance_minor < 0:
            debt_minor += -balance.balance_minor
            continue
        category = finance_service.ASSET_CATEGORY_BY_TYPE.get(balance.account_type)
        if category is not None:
            totals[category] = totals.get(category, 0) + balance.balance_minor
    breakdown = [
        AssetCategoryTotal(
            category=category, total=MoneySchema(amount_minor=totals[category], currency=currency)
        )
        for category in finance_service.ASSET_CATEGORY_ORDER
        if category in totals
    ]
    return breakdown, MoneySchema(amount_minor=debt_minor, currency=currency)


def _spending_insights(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> SpendingInsights:
    """M42: month-to-date outflow vs the same day range last month, plus top merchants."""
    today = today or date.today()
    this_start = today.replace(day=1)

    # Same day range in the prior month, clamped to the prior month's length so
    # comparing e.g. Mar 31 never runs off the end of February.
    prev_last = this_start - timedelta(days=1)
    prev_start = prev_last.replace(day=1)
    prev_end = min(prev_start + timedelta(days=today.day - 1), prev_last)

    this_minor = repository.sum_spending(engine, household_id, this_start, today, currency)
    last_minor = repository.sum_spending(engine, household_id, prev_start, prev_end, currency)
    change = None if last_minor == 0 else round((this_minor - last_minor) / last_minor * 100)

    merchants = [
        MerchantSpend(
            merchant=m.merchant,
            amount=MoneySchema(amount_minor=m.amount_minor, currency=currency),
        )
        for m in repository.top_spending_merchants(
            engine, household_id, this_start, today, currency, limit=5
        )
    ]
    return SpendingInsights(
        this_month=MoneySchema(amount_minor=this_minor, currency=currency),
        last_month=MoneySchema(amount_minor=last_minor, currency=currency),
        change_percent=change,
        top_merchants=merchants,
    )


def _top_goal(engine: Engine, household_id: str) -> GoalProgress | None:
    """M41: the highest-priority goal (list_goals is priority-ordered) with progress."""
    goals = repository.list_goals(engine, household_id)
    if not goals:
        return None
    goal = goals[0]
    percent = 0
    if goal.target_minor > 0:
        percent = min(100, round(goal.current_minor / goal.target_minor * 100))
    return GoalProgress(
        id=goal.id,
        name=goal.name,
        type=goal.goal_type,
        current=MoneySchema(amount_minor=goal.current_minor, currency=goal.currency),
        target=MoneySchema(amount_minor=goal.target_minor, currency=goal.currency),
        percent_complete=percent,
        target_date=goal.target_date,
    )


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
    currency = household.base_currency

    net_worth_result = finance_service.compute_net_worth(engine, household.id, currency)
    emergency_fund_result = finance_service.compute_emergency_fund(engine, household.id, currency)
    months = emergency_fund_result.outputs["emergency_fund_months"]

    ef_inputs = finance_service.emergency_fund_inputs(engine, household.id, currency)
    income = finance_service.monthly_income_total(engine, household.id, currency)
    bills = finance_service.monthly_bill_total(engine, household.id, currency)
    asset_breakdown, total_debt = _asset_and_debt_summary(engine, household.id, currency)
    upcoming = [
        UpcomingBill(
            id=bill.id,
            name=bill.name,
            amount=MoneySchema(amount_minor=bill.amount.amount_minor, currency=bill.amount.currency),
            due_date=bill.due_date,
            days_until=bill.days_until,
        )
        for bill in finance_service.upcoming_bills(engine, household.id, currency)
    ]
    history = [
        NetWorthPoint(
            as_of=snapshot.as_of,
            net_worth=MoneySchema(amount_minor=snapshot.net_worth_minor, currency=snapshot.currency),
        )
        for snapshot in repository.list_net_worth_snapshots(engine, household.id, limit=30)
    ]

    return HouseholdContext(
        household_id=household.id,
        display_name=household.display_name,
        currency=currency,
        net_worth=MoneySchema(**net_worth_result.outputs["net_worth"].to_dict()),
        emergency_fund_months=months,
        emergency_fund=_emergency_fund_summary(months, ef_inputs, currency),
        monthly_cash_flow=MonthlyCashFlow(
            income=MoneySchema(amount_minor=income.amount_minor, currency=currency),
            bills=MoneySchema(amount_minor=bills.amount_minor, currency=currency),
            net=MoneySchema(
                amount_minor=income.amount_minor - bills.amount_minor, currency=currency
            ),
        ),
        asset_breakdown=asset_breakdown,
        total_debt=total_debt,
        upcoming_bills=upcoming,
        net_worth_history=history,
        top_goal=_top_goal(engine, household.id),
        spending_insights=_spending_insights(engine, household.id, currency),
    )
