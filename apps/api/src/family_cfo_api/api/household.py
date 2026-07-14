from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import audit, finance_service, repository
from family_cfo_api.api.budgets import _month_window, budgets_with_progress
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    AssetCategoryTotal,
    BudgetSummary,
    EmergencyFundSummary,
    ErrorResponse,
    GoalProgress,
    HouseholdContext,
    HouseholdUpdateRequest,
    MerchantSpend,
    CategorySpend,
    MonthlyCashFlow,
    NetWorthPoint,
    SafeToSpend,
    SavingsRate,
    SpendingByCategory,
    SpendingInsights,
    UpcomingBill,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Household"])


# M75: severity order for combining months-based and goal-based statuses —
# the card must never look rosier than the WORSE of the two views.
_EF_STATUS_RANK = {"no_fund": 0, "getting_started": 1, "on_track": 2, "fully_funded": 3}


def _emergency_fund_summary(
    months: float | None,
    inputs: finance_service.EmergencyFundInputs,
    currency: str,
    target_months: float | None = None,
    goal_target_minor: int | None = None,
) -> EmergencyFundSummary:
    """M38/M43/M75: coverage vs the target months AND the emergency-fund goal.

    Months-of-bills alone is absurdly optimistic when few bills are entered
    (a $1k fund "covers" months of a $15 Netflix bill); a declared
    emergency-fund GOAL is the family's own target, so the final status is
    the more conservative of the two views and the gap is the larger one.
    """
    recommended = target_months or finance_service.EMERGENCY_FUND_TARGET_RECOMMENDED_MONTHS
    # M43: a sub-3-month target still needs a sensible "getting started" floor.
    min_threshold = min(finance_service.EMERGENCY_FUND_TARGET_MIN_MONTHS, recommended)
    fund_minor = inputs.fund.amount_minor
    bills_minor = inputs.monthly_bills.amount_minor

    months_status: str | None = None
    months_gap_minor = 0
    if months is not None:
        months_gap_minor = max(0, round(recommended * bills_minor) - fund_minor)
        if fund_minor <= 0:
            months_status = "no_fund"
        elif months < min_threshold:
            months_status = "getting_started"
        elif months < recommended:
            months_status = "on_track"
        else:
            months_status = "fully_funded"

    goal_status: str | None = None
    goal_gap_minor = 0
    if goal_target_minor and goal_target_minor > 0:
        goal_gap_minor = max(0, goal_target_minor - fund_minor)
        ratio = fund_minor / goal_target_minor
        if fund_minor <= 0:
            goal_status = "no_fund"
        elif ratio < 0.5:
            goal_status = "getting_started"
        elif ratio < 1.0:
            goal_status = "on_track"
        else:
            goal_status = "fully_funded"

    candidates = [s for s in (months_status, goal_status) if s is not None]
    if candidates:
        status = min(candidates, key=lambda s: _EF_STATUS_RANK[s])
        gap = MoneySchema(
            amount_minor=max(months_gap_minor, goal_gap_minor), currency=currency
        )
    else:
        status = "no_bills"
        gap = None

    return EmergencyFundSummary(
        months=months,
        reserved=MoneySchema(amount_minor=fund_minor, currency=currency),
        using_designations=inputs.using_designations,
        monthly_expenses=MoneySchema(amount_minor=bills_minor, currency=currency),
        target_months_min=min_threshold,
        target_months_recommended=recommended,
        gap_to_recommended=gap,
        goal_target=(
            MoneySchema(amount_minor=goal_target_minor, currency=currency)
            if goal_target_minor
            else None
        ),
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


def _savings_rate(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> SavingsRate:
    """M44: recurring income vs trailing-3-complete-month average actual spending."""
    today = today or date.today()
    this_month_start = today.replace(day=1)
    # The last 3 complete calendar months (exclude the current partial month).
    window_start = finance_service.add_months(this_month_start, -3)
    window_end = this_month_start - timedelta(days=1)

    income = finance_service.monthly_income_total(engine, household_id, currency).amount_minor
    spending_3mo = repository.sum_spending(engine, household_id, window_start, window_end, currency)
    avg_spending = round(spending_3mo / 3)

    percent = None if income <= 0 else round((income - avg_spending) / income * 100)
    return SavingsRate(
        percent=percent,
        monthly_income=MoneySchema(amount_minor=income, currency=currency),
        average_monthly_spending=MoneySchema(amount_minor=avg_spending, currency=currency),
    )


def _budget_summary(engine: Engine, household_id: str, currency: str) -> BudgetSummary | None:
    """M46: envelope health for the Overview; None when no budgets exist."""
    budgets = budgets_with_progress(engine, household_id, currency)
    if not budgets:
        return None
    return BudgetSummary(
        envelope_count=len(budgets),
        over_count=sum(1 for b in budgets if b.status == "over"),
        warning_count=sum(1 for b in budgets if b.status == "warning"),
        total_budgeted=MoneySchema(
            amount_minor=sum(b.limit.amount_minor for b in budgets), currency=currency
        ),
        total_spent=MoneySchema(
            amount_minor=sum(b.spent.amount_minor for b in budgets), currency=currency
        ),
    )


_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _spending_by_category(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> SpendingByCategory | None:
    """M94: this month's outflow grouped by category — the payoff of categorizing.
    None when nothing has been spent this month (nothing to show)."""
    today = today or date.today()
    start, end = _month_window(today)

    total = repository.sum_spending(engine, household_id, start, end, currency)
    if total == 0:
        return None

    by_category = repository.sum_spending_by_category(engine, household_id, start, end, currency)
    names = {c.id: c.name for c in repository.list_categories(engine, household_id)}

    categorized_minor = sum(by_category.values())
    entries = [
        CategorySpend(
            category_id=cid,
            # A category deleted after the spend still has transactions; label it
            # rather than drop the money.
            category_name=names.get(cid, "Uncategorized"),
            amount=MoneySchema(amount_minor=minor, currency=currency),
        )
        for cid, minor in by_category.items()
    ]
    entries.sort(key=lambda e: e.amount.amount_minor, reverse=True)

    return SpendingByCategory(
        month=f"{today.year}-{today.month:02d}",
        month_label=f"{_MONTHS[today.month - 1]} {today.year}",
        categories=entries,
        categorized_total=MoneySchema(amount_minor=categorized_minor, currency=currency),
        uncategorized=MoneySchema(amount_minor=total - categorized_minor, currency=currency),
    )


def _safe_to_spend(engine: Engine, household_id: str, currency: str) -> SafeToSpend | None:
    """M93: what's free to spend now, for the Overview. None when there is no
    liquid balance to reason about (a brand-new household)."""
    result, _ref = finance_service.compute_safe_to_spend(engine, household_id, currency)
    out = result.outputs

    def money(key: str) -> MoneySchema:
        m = out[key]
        return MoneySchema(amount_minor=m.amount_minor, currency=m.currency)

    if out["liquid_balance"].amount_minor == 0 and out["committed_total"].amount_minor == 0:
        return None
    return SafeToSpend(
        liquid_balance=money("liquid_balance"),
        emergency_fund_reserved=money("emergency_fund_reserved"),
        bills_due=money("bills_due"),
        minimum_debt_payments=money("minimum_debt_payments"),
        committed_total=money("committed_total"),
        safe_to_spend=money("safe_to_spend"),
        total_debt=money("total_debt"),
        warnings=list(result.warnings),
    )


def _top_goal(engine: Engine, household_id: str) -> GoalProgress | None:
    """M41: the highest-priority goal (list_goals is priority-ordered) with progress."""
    goals = repository.list_goals(engine, household_id)
    if not goals:
        return None
    goal = goals[0]
    current_minor = finance_service.goal_current_minor(engine, household_id, goal)
    percent = 0
    if goal.target_minor > 0:
        percent = min(100, round(current_minor / goal.target_minor * 100))
    return GoalProgress(
        id=goal.id,
        name=goal.name,
        type=goal.goal_type,
        current=MoneySchema(amount_minor=current_minor, currency=goal.currency),
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
        emergency_fund=_emergency_fund_summary(
            months,
            ef_inputs,
            currency,
            household.emergency_fund_target_months,
            # M75: the family's own emergency-fund goal is the target of
            # record; with several, the LARGEST target is the conservative one.
            goal_target_minor=max(
                (
                    g.target_minor
                    for g in repository.list_goals(engine, household.id)
                    if g.goal_type == "emergency_fund"
                ),
                default=None,
            ),
        ),
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
        savings_rate=_savings_rate(engine, household.id, currency),
        budget_summary=_budget_summary(engine, household.id, currency),
        safe_to_spend=_safe_to_spend(engine, household.id, currency),
        spending_by_category=_spending_by_category(engine, household.id, currency),
    )


@router.patch(
    "/household",
    operation_id="updateHousehold",
    response_model=HouseholdContext,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Household not found", "model": ErrorResponse},
    },
    summary="Update household settings (M43: emergency-fund target)",
)
async def update_household(
    payload: HouseholdUpdateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> HouseholdContext:
    if repository.get_household(engine, session.household_id) is None:
        raise HTTPException(status_code=404, detail="Household not found")

    if payload.clear_emergency_fund_target:
        target: float | None = None
    elif payload.emergency_fund_target_months is not None:
        target = payload.emergency_fund_target_months
    else:
        # Nothing to change; return the current context unchanged.
        return await get_household_context(session=session, engine=engine)

    repository.update_emergency_fund_target(engine, session.household_id, target)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "household.updated",
        "household",
        session.household_id,
        f"Set emergency-fund target to {target if target is not None else 'default'}",
    )
    return await get_household_context(session=session, engine=engine)
