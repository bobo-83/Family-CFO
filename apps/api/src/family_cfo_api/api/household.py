from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import audit, finance_service, repository, rights, undo_actions
from family_cfo_api.api.budgets import _month_window, budgets_with_progress
from family_cfo_api.deps import get_current_session, get_engine, require_right
from family_cfo_api import yearly_review as yearly_review_module
from family_cfo_api.schemas import (
    CashOutlookResponse,
    YearlyOverview,
    YearlyReview,
    YearMonthSummary,
    OutlookEvent,
    SpendingPlanResponse,
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
    LiquidAccountBalance,
    NamedAmount,
    SafeToSpend,
    SavingsRate,
    SpendingByCategory,
    SpendingInsights,
    UpcomingBill,
)
from family_cfo_api.schemas import Money as MoneySchema
from family_cfo_financial_engine.money import Money

router = APIRouter(tags=["Household"])


# M75: severity order for combining months-based and goal-based statuses —
# the card must never look rosier than the WORSE of the two views.
_EF_STATUS_RANK = {"no_fund": 0, "getting_started": 1, "on_track": 2, "fully_funded": 3}


def _emergency_fund_summary(
    months: float | None,
    inputs: finance_service.EmergencyFundInputs,
    monthly_expenses: Money,
    currency: str,
    target_months: float | None = None,
    goal_target_minor: int | None = None,
) -> EmergencyFundSummary:
    """M38/M43/M75/ADR 0039: coverage vs the target months AND the emergency-fund goal.

    ``monthly_expenses`` is the realistic monthly need (bills + debt minimums +
    everyday spending above bills), not bills alone — dividing by bills alone was
    absurdly optimistic. A declared emergency-fund GOAL is the family's own target,
    so the final status is the more conservative of the two views and the gap is the
    larger one.
    """
    recommended = target_months or finance_service.EMERGENCY_FUND_TARGET_RECOMMENDED_MONTHS
    # M43: a sub-3-month target still needs a sensible "getting started" floor.
    min_threshold = min(finance_service.EMERGENCY_FUND_TARGET_MIN_MONTHS, recommended)
    fund_minor = inputs.fund.amount_minor
    expenses_minor = monthly_expenses.amount_minor

    months_status: str | None = None
    months_gap_minor = 0
    if months is not None:
        months_gap_minor = max(0, round(recommended * expenses_minor) - fund_minor)
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
        monthly_expenses=MoneySchema(amount_minor=expenses_minor, currency=currency),
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
    balances = repository.list_account_balances(engine, household_id)
    liquid_accounts = [
        LiquidAccountBalance(
            name=balance.name,
            balance=MoneySchema(amount_minor=balance.balance_minor, currency=balance.currency),
        )
        for balance in balances
        if balance.account_type in finance_service.LIQUID_ACCOUNT_TYPES
        and balance.currency == currency
    ]
    minimum_debt_items = [
        NamedAmount(
            name=debt.name,
            amount=MoneySchema(amount_minor=debt.minimum_payment_minor, currency=debt.currency),
        )
        for debt in repository.list_debts_with_terms(engine, household_id)
        if debt.currency == currency and debt.minimum_payment_minor > 0
    ]
    household = repository.get_household(engine, household_id)
    card_items: list[NamedAmount] = []
    if household is not None and household.credit_cards_paid_in_full:
        card_items = [
            NamedAmount(
                name=balance.name,
                amount=MoneySchema(amount_minor=-balance.balance_minor, currency=balance.currency),
            )
            for balance in balances
            if balance.account_type == "credit_card"
            and balance.currency == currency
            and balance.balance_minor < 0
        ]
    bill_items = [
        NamedAmount(
            name=bill.name,
            amount=MoneySchema(
                amount_minor=bill.amount.amount_minor, currency=bill.amount.currency),
        )
        for bill in finance_service.upcoming_bills(
            engine,
            household_id,
            currency,
            window_days=finance_service.SAFE_TO_SPEND_HORIZON_DAYS,
        )
        if bill.amount.currency == currency
    ]
    emergency_fund_items = [
        NamedAmount(
            name=balance.name,
            amount=MoneySchema(
                amount_minor=repository.emergency_fund_reserved_minor(
                    balance.emergency_fund_percent,
                    balance.emergency_fund_minor,
                    balance.balance_minor,
                ),
                currency=balance.currency,
            ),
        )
        for balance in balances
        if balance.currency == currency
        and (balance.emergency_fund_percent is not None or balance.emergency_fund_minor is not None)
        and repository.emergency_fund_reserved_minor(
            balance.emergency_fund_percent, balance.emergency_fund_minor, balance.balance_minor
        )
        > 0
    ]
    forecast_items, _ = finance_service.subscription_forecast(engine, household_id, currency)
    subscription_forecast_items = [
        NamedAmount(
            name=item.name,
            amount=MoneySchema(amount_minor=item.amount_minor, currency=item.currency),
        )
        for item in forecast_items
    ]
    # The engine emits a guardrail warning ("Spendable cash must be reported
    # alongside that debt, never on its own") to keep the ADVISOR from quoting
    # safe-to-spend without context. It's not a user heads-up — it just repeats the
    # total debt (which lives on the Debts tab / net worth), so keep it out of the UI.
    user_warnings = [
        w for w in result.warnings if "reported alongside that debt" not in w
    ]
    card_payments = out.get("credit_card_payments")
    forecast = out.get("subscription_forecast")
    return SafeToSpend(
        liquid_balance=money("liquid_balance"),
        emergency_fund_reserved=money("emergency_fund_reserved"),
        bills_due=money("bills_due"),
        minimum_debt_payments=money("minimum_debt_payments"),
        credit_card_payments=(
            MoneySchema(amount_minor=card_payments.amount_minor, currency=card_payments.currency)
            if card_payments is not None and card_payments.amount_minor > 0
            else None
        ),
        subscription_forecast=(
            MoneySchema(amount_minor=forecast.amount_minor, currency=forecast.currency)
            if forecast is not None and forecast.amount_minor > 0
            else None
        ),
        committed_total=money("committed_total"),
        safe_to_spend=money("safe_to_spend"),
        total_debt=money("total_debt"),
        warnings=user_warnings,
        liquid_accounts=liquid_accounts,
        minimum_debt_items=minimum_debt_items,
        credit_card_items=card_items,
        bill_items=bill_items,
        emergency_fund_items=emergency_fund_items,
        subscription_forecast_items=subscription_forecast_items,
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
    month: str | None = None,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> HouseholdContext:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")

    current_month = f"{date.today().year}-{date.today().month:02d}"
    if month is not None and month != current_month:
        # A past month is computed LIVE from transactions (so recategorizing it
        # updates the breakdown); the 'now-only' cards that can't be reconstructed
        # are left off.
        return _historical_context(engine, household, month)
    return _build_household_context(engine, household)


@router.get(
    "/overview/yearly",
    operation_id="getYearlyOverview",
    response_model=YearlyOverview,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Household not found", "model": ErrorResponse},
    },
    summary="The year at a glance: monthly trend, totals, top categories, cached review",
)
async def get_yearly_overview(
    year: int | None = None,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> YearlyOverview:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")
    today = repository.utcnow().date()
    resolved_year = year or today.year
    months, top = yearly_review_module.build_year_overview(
        engine, household.id, household.base_currency, resolved_year, today=today
    )
    currency = household.base_currency

    def money(minor: int) -> MoneySchema:
        return MoneySchema(amount_minor=minor, currency=currency)

    cached = repository.get_yearly_review(engine, household.id, resolved_year)
    review = None
    if cached is not None:
        review = YearlyReview(
            summary=cached.summary,
            suggestions=cached.suggestions,
            months_covered=cached.months_covered,
            model=cached.model,
            generated_at=cached.created_at,
        )
    return YearlyOverview(
        year=resolved_year,
        months=[
            YearMonthSummary(
                month=m.month,
                income=money(m.income_minor),
                spending=money(m.spending_minor),
                net=money(m.net_minor),
                net_worth_eom=money(m.net_worth_eom_minor)
                if m.net_worth_eom_minor is not None
                else None,
            )
            for m in months
        ],
        total_income=money(sum(m.income_minor for m in months)),
        total_spending=money(sum(m.spending_minor for m in months)),
        total_net=money(sum(m.net_minor for m in months)),
        top_categories=[
            NamedAmount(name=name, amount=money(amount)) for name, amount in top
        ],
        review=review,
    )


@router.post(
    "/overview/yearly/review",
    operation_id="generateYearlyReview",
    response_model=YearlyReview,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Household not found", "model": ErrorResponse},
    },
    summary="(Re)generate the year's grounded narrative and suggestions",
)
async def generate_yearly_review(
    year: int | None = None,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> YearlyReview:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")
    today = repository.utcnow().date()
    resolved_year = year or today.year
    result = yearly_review_module.generate_review(
        engine, household.id, household.base_currency, resolved_year, today=today
    )
    cached = repository.get_yearly_review(engine, household.id, resolved_year)
    return YearlyReview(
        summary=result["summary"],
        suggestions=result["suggestions"],
        months_covered=result["months_covered"],
        model=result["model"],
        generated_at=cached.created_at if cached else repository.utcnow(),
    )


@router.get(
    "/overview/cash-outlook",
    operation_id="getCashOutlook",
    response_model=CashOutlookResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Household not found", "model": ErrorResponse},
    },
    summary="Projected cash over the next 30 days — paychecks in, payments out",
)
async def get_cash_outlook(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> CashOutlookResponse:
    """M112 (ADR 0026): the lived counterpart to safe-to-spend's zero-income
    stress test — expected paydays (from recurring-income detection) and every
    timeline payment, projected day by day, with the lowest point reached. Also
    repeats the Bills due-vs-cash headline so both screens say the same thing."""
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")
    currency = household.base_currency
    outlook = finance_service.cash_outlook(engine, session.household_id, currency)
    headline = finance_service.payment_timeline(engine, session.household_id, currency)

    def money(minor: int) -> MoneySchema:
        return MoneySchema(amount_minor=minor, currency=currency)

    return CashOutlookResponse(
        starting_cash=money(outlook.starting_cash_minor),
        events=[
            OutlookEvent(
                occurred_on=event.occurred_on,
                name=event.name,
                amount=money(event.amount_minor),
                kind=event.kind,
            )
            for event in outlook.events
        ],
        ending_cash=money(outlook.ending_cash_minor),
        lowest_balance=money(outlook.lowest_minor),
        lowest_date=outlook.lowest_date,
        expected_income=money(outlook.expected_income_minor),
        obligations=money(outlook.obligations_minor),
        horizon_days=outlook.horizon_days,
        due_soon=money(headline.due_total_minor),
        due_soon_covered=headline.covered,
        due_soon_window_days=headline.window_days,
    )


@router.get(
    "/overview/spending-plan",
    operation_id="getSpendingPlan",
    response_model=SpendingPlanResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Household not found", "model": ErrorResponse},
    },
    summary="Left to spend this month — income minus spent and committed",
)
async def get_spending_plan(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> SpendingPlanResponse:
    """M113 (ADR 0027): the month plan. Expected income (received + projected
    paydays) minus month-to-date spending, unpaid bills through month end, and
    the month's loan/lease payments. Terms never overlap — see the service."""
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")
    currency = household.base_currency
    plan = finance_service.spending_plan(engine, session.household_id, currency)

    def money(minor: int) -> MoneySchema:
        return MoneySchema(amount_minor=minor, currency=currency)

    return SpendingPlanResponse(
        month=plan.month,
        income_received=money(plan.income_received_minor),
        income_projected=money(plan.income_projected_minor),
        expected_income=money(plan.expected_income_minor),
        spent=money(plan.spent_minor),
        bills_remaining=money(plan.bills_remaining_minor),
        account_obligations=money(plan.account_obligations_minor),
        planned_savings=money(plan.planned_savings_minor),
        left_to_spend=money(plan.left_minor),
        per_day=money(plan.per_day_minor),
        days_remaining=plan.days_remaining,
    )


def _historical_context(
    engine: Engine, household: repository.HouseholdRecord, month: str
) -> HouseholdContext:
    """A past month, rebuilt from the data we have: that month's spending and income
    (live, so recategorizing updates them) and net worth reconstructed from
    transactions. 'Right now' cards (safe-to-spend, upcoming bills, emergency fund)
    can't be reconstructed from point-in-time balances, so they're left off."""
    currency = household.base_currency
    anchor = date(int(month[:4]), int(month[5:7]), 1)
    month_start, month_end = _month_window(anchor)

    # Prefer an accurate daily snapshot; fall back to reconstructing from transactions.
    net_worth_minor = repository.net_worth_as_of(engine, household.id, month_end, currency)
    if net_worth_minor == 0:
        net_worth_minor = finance_service.reconstruct_net_worth(
            engine, household.id, month_end, currency
        )

    month_income = repository.sum_income(engine, household.id, month_start, month_end, currency)
    bills = finance_service.monthly_bill_total(engine, household.id, currency)
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
        net_worth=MoneySchema(amount_minor=net_worth_minor, currency=currency),
        # Required, non-nullable in the contract (the client decodes a plain Double,
        # so `null` would fail to decode and the whole month would silently fail to
        # load). The emergency-fund *card* is a "now" concept and stays hidden for a
        # past month (its EmergencyFundSummary is omitted); 0 is just a safe filler.
        emergency_fund_months=0.0,
        net_worth_history=history,
        # That month's actual money in, against the recurring bills.
        monthly_cash_flow=MonthlyCashFlow(
            income=MoneySchema(amount_minor=month_income, currency=currency),
            bills=MoneySchema(amount_minor=bills.amount_minor, currency=currency),
            net=MoneySchema(amount_minor=month_income - bills.amount_minor, currency=currency),
        ),
        spending_by_category=_spending_by_category(engine, household.id, currency, today=anchor),
        earliest_month=repository.earliest_transaction_month(engine, household.id),
    )


def _build_household_context(
    engine: Engine, household: repository.HouseholdRecord
) -> HouseholdContext:
    currency = household.base_currency

    synced_times = [
        c.last_synced_at
        for c in repository.list_institution_connections(engine, household.id)
        if c.last_synced_at is not None
    ]
    last_synced_at = max(synced_times) if synced_times else None

    net_worth_result = finance_service.compute_net_worth(engine, household.id, currency)
    emergency_fund_result = finance_service.compute_emergency_fund(engine, household.id, currency)
    months = emergency_fund_result.outputs["emergency_fund_months"]

    ef_inputs = finance_service.emergency_fund_inputs(engine, household.id, currency)
    income = finance_service.monthly_income_total(engine, household.id, currency)
    income_baseline = finance_service.w2_baseline_monthly(engine, household.id, currency)
    taxes = finance_service.monthly_taxes_total(engine, household.id, currency)
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
            finance_service.monthly_essential_expenses(engine, household.id, currency),
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
            income_baseline=(
                MoneySchema(amount_minor=income_baseline.amount_minor, currency=currency)
                if income_baseline is not None
                else None
            ),
            taxes=(
                MoneySchema(amount_minor=taxes.amount_minor, currency=currency)
                if taxes.amount_minor > 0
                else None
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
        last_synced_at=last_synced_at,
        earliest_month=repository.earliest_transaction_month(engine, household.id),
        review_count=repository.count_review_transactions(engine, household.id),
    )


@router.get(
    "/spending",
    operation_id="getSpendingByCategory",
    response_model=SpendingByCategory,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        422: {"description": "Invalid month", "model": ErrorResponse},
    },
    summary="Spending by category for a month (defaults to the current month)",
)
async def get_spending_by_category(
    month: str | None = None,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> SpendingByCategory:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")
    currency = household.base_currency

    anchor = date.today()
    if month is not None:
        try:
            year_str, month_str = month.split("-")
            anchor = date(int(year_str), int(month_str), 1)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="month must be YYYY-MM") from exc

    result = _spending_by_category(engine, session.household_id, currency, today=anchor)
    if result is not None:
        return result
    # A month with no spending still returns its (empty) shape so the switcher can
    # show the label and "nothing spent".
    zero = MoneySchema(amount_minor=0, currency=currency)
    return SpendingByCategory(
        month=f"{anchor.year}-{anchor.month:02d}",
        month_label=f"{_MONTHS[anchor.month - 1]} {anchor.year}",
        categories=[],
        categorized_total=zero,
        uncategorized=zero,
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
    session: repository.SessionContext = Depends(require_right(rights.HOUSEHOLD_SETTINGS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> HouseholdContext:
    before = repository.get_household(engine, session.household_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Household not found")

    changed: list[str] = []

    if payload.clear_emergency_fund_target or payload.emergency_fund_target_months is not None:
        target = None if payload.clear_emergency_fund_target else payload.emergency_fund_target_months
        repository.update_emergency_fund_target(engine, session.household_id, target)
        changed.append(f"emergency-fund target to {target if target is not None else 'default'}")

    if payload.credit_cards_paid_in_full is not None:
        repository.set_credit_cards_paid_in_full(
            engine, session.household_id, payload.credit_cards_paid_in_full
        )
        changed.append(f"credit-cards-paid-in-full to {payload.credit_cards_paid_in_full}")

    if changed:
        audit.write_audit(
            engine,
            session.household_id,
            session.user_id,
            "household.updated",
            "household",
            session.household_id,
            "Set " + "; ".join(changed),
            undo_token=undo_actions.household_updated(before),
        )
    return await get_household_context(session=session, engine=engine)
