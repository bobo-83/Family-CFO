import logging
import os
from datetime import date, timedelta

from family_cfo_financial_engine.money import Money
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import (
    audit,
    finance_service,
    household_crypto,
    repository,
    rights,
    undo_actions,
)
from family_cfo_api import yearly_review as yearly_review_module
from family_cfo_api.api.budgets import _month_window, budgets_with_progress
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine, require_right
from family_cfo_api.schemas import (
    AssetCategoryTotal,
    BudgetSummary,
    CashOutlookResponse,
    CategorySpend,
    DeviceWrap,
    EmergencyFundSummary,
    ErrorResponse,
    GoalProgress,
    HouseholdContext,
    HouseholdKeyStatus,
    HouseholdUpdateRequest,
    KeySessionRequest,
    LiquidAccountBalance,
    MerchantSpend,
    MonthlyCashFlow,
    NamedAmount,
    NetWorthPoint,
    OutlookEvent,
    ReadyToSellHoldings,
    RecoveryKey,
    RecoveryUnlockRequest,
    SafeToSpend,
    SavingsContribution,
    SavingsContributionCreateRequest,
    SavingsContributionDismissRequest,
    SavingsContributionUpdateRequest,
    SavingsRate,
    SealModeRequest,
    SpendingByCategory,
    SpendingInsights,
    SpendingPlanResponse,
    UpcomingBill,
    YearlyOverview,
    YearlyReview,
    YearMonthSummary,
)
from family_cfo_api.schemas import Money as MoneySchema

logger = logging.getLogger(__name__)

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


def _ready_to_sell(engine: Engine, household_id: str, currency: str) -> ReadyToSellHoldings | None:
    """The "vested RSUs, one sale from cash" line beside safe-to-spend: the
    provider-synced balances of the accounts the user tagged. The balance is
    ground truth — sales and price moves included — which is why the tag lives
    on the account instead of deriving from the vest schedule."""
    total = 0
    items: list[NamedAmount] = []
    for balance in repository.list_account_balances(engine, household_id):
        if not balance.rsu_ready_to_sell or balance.currency != currency:
            continue
        if balance.balance_minor <= 0:
            continue
        total += balance.balance_minor
        items.append(
            NamedAmount(
                name=balance.name,
                amount=MoneySchema(amount_minor=balance.balance_minor, currency=currency),
            )
        )
    if not items:
        return None
    return ReadyToSellHoldings(
        value=MoneySchema(amount_minor=total, currency=currency),
        accounts=items,
        sale_notice_business_days=finance_service.RSU_SALE_NOTICE_BUSINESS_DAYS,
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
    savings = out.get("committed_savings")
    _savings_total, savings_raw = finance_service.committed_savings_in_window(
        engine, household_id, currency,
        today=date.today(),
        horizon_days=finance_service.SAFE_TO_SPEND_HORIZON_DAYS,
    )
    savings_items = [
        NamedAmount(
            name=f"{name} — due {due.strftime('%b %-d')}",
            amount=MoneySchema(amount_minor=amount.amount_minor, currency=amount.currency),
        )
        for name, amount, due in savings_raw
    ]
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
        ready_to_sell=_ready_to_sell(engine, household_id, currency),
        liquid_accounts=liquid_accounts,
        minimum_debt_items=minimum_debt_items,
        credit_card_items=card_items,
        bill_items=bill_items,
        emergency_fund_items=emergency_fund_items,
        subscription_forecast_items=subscription_forecast_items,
        committed_savings=(
            MoneySchema(amount_minor=savings.amount_minor, currency=savings.currency)
            if savings is not None and savings.amount_minor > 0
            else None
        ),
        committed_savings_items=savings_items,
        committed_savings_reserved=bool(out.get("committed_savings_reserved")),
    )


# #4: destination type -> the goal type it plausibly funds. Only unambiguous
# matches are suggested (exactly one goal of that type).
_GOAL_TYPE_FOR_DESTINATION = {"529": "college", "retirement": "retirement"}


def _suggested_goal(goals_by_type: dict[str, list], candidate) -> str | None:
    if candidate.goal_id is not None:
        return None
    goal_type = _GOAL_TYPE_FOR_DESTINATION.get(candidate.destination_type)
    if goal_type is None:
        return None
    matches = goals_by_type.get(goal_type, [])
    return matches[0].id if len(matches) == 1 else None


def _savings_contributions(engine: Engine, household_id: str) -> list[SavingsContribution]:
    """#201: recurring transfers into savings vehicles. Best-effort — detection
    must never break the Overview, so a failure yields an empty list."""
    from family_cfo_api import savings_detection

    try:
        found = savings_detection.detect_for_household(engine, household_id)
    except Exception:
        logger.exception("savings detection failed household=%s", household_id)
        return []
    goals_by_type: dict[str, list] = {}
    for goal in repository.list_goals(engine, household_id):
        goals_by_type.setdefault(goal.goal_type, []).append(goal)
    return [
        SavingsContribution(
            destination_name=c.destination_name,
            destination_type=c.destination_type,
            amount=MoneySchema(amount_minor=c.amount_minor, currency=c.currency),
            frequency=c.frequency,
            monthly_equivalent=MoneySchema(
                amount_minor=savings_detection.monthly_equivalent_minor(c),
                currency=c.currency,
            ),
            occurrences=c.occurrences,
            last_seen=c.last_seen,
            inferred=c.inferred,
            declared=c.declared,
            contribution_id=c.contribution_id,
            source_account_id=c.source_account_id,
            destination_account_id=c.destination_account_id or "",
            goal_id=c.goal_id,
            suggested_goal_id=_suggested_goal(goals_by_type, c) if c.declared else None,
        )
        for c in found
    ]


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

    # ADR 0069 + M-rsu-grants: the sell-by instruction, and — with grants and a
    # live quote — the shortfall translated into whole shares to sell.
    runway_action = None
    sell_units = None
    sell_ticker = None
    if outlook.sell_by_date is not None:
        from family_cfo_api import rsu_service

        profiles = rsu_service.effective_income_profiles(engine, session.household_id)
        runway_action = (
            "sell_rsus" if any(p.rsu_annual_minor > 0 for p in profiles) else "move_cash"
        )
        if runway_action == "sell_rsus" and outlook.first_shortfall_date is not None:
            valuation = rsu_service.load_valuation(engine, session.household_id)
            grant = valuation.grants[0] if valuation.grants else None
            quote = valuation.quotes.get(grant.ticker) if grant else None
            if quote and quote.price_minor > 0:
                shortfall_minor = -outlook.lowest_minor
                sell_units = -(-shortfall_minor // quote.price_minor)  # ceil
                sell_ticker = grant.ticker

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
        first_shortfall_date=outlook.first_shortfall_date,
        # The DEEPEST gap, not the first crossing: selling only enough for the
        # first shortfall would leave later payments uncovered (ADR 0069).
        shortfall=(
            money(-outlook.lowest_minor) if outlook.first_shortfall_date is not None else None
        ),
        sell_by_date=outlook.sell_by_date,
        runway_action=runway_action,
        sell_units=sell_units,
        sell_ticker=sell_ticker,
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
    month_spending = repository.sum_spending(
        engine, household.id, month_start, month_end, currency
    )
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
        language=household.language or "en",
        reserve_committed_savings=household.reserve_committed_savings,
        net_worth=MoneySchema(amount_minor=net_worth_minor, currency=currency),
        # Required, non-nullable in the contract (the client decodes a plain Double,
        # so `null` would fail to decode and the whole month would silently fail to
        # load). The emergency-fund *card* is a "now" concept and stays hidden for a
        # past month (its EmergencyFundSummary is omitted); 0 is just a safe filler.
        emergency_fund_months=0.0,
        net_worth_history=history,
        # That month's actual money in and out (the Year chart's rule).
        monthly_cash_flow=MonthlyCashFlow(
            income=MoneySchema(amount_minor=month_income, currency=currency),
            spending=MoneySchema(amount_minor=month_spending, currency=currency),
            net=MoneySchema(amount_minor=month_income - month_spending, currency=currency),
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
    # Month-to-date SPENDING, the Year chart's own rule — the recurring-bill
    # model here read "$208 Bills" against $22k of real outflow and made the
    # card nonsense (user report 2026-07-25).
    today = date.today()
    month_spending = repository.sum_spending(
        engine, household.id, today.replace(day=1), today, currency
    )
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
        language=household.language or "en",
        reserve_committed_savings=household.reserve_committed_savings,
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
            spending=MoneySchema(amount_minor=month_spending, currency=currency),
            net=MoneySchema(
                amount_minor=income.amount_minor - month_spending, currency=currency
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
        savings_contributions=_savings_contributions(engine, household.id),
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
        422: {"description": "Unsupported language", "model": ErrorResponse},
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

    if payload.reserve_committed_savings is not None:
        repository.set_reserve_committed_savings(
            engine, session.household_id, payload.reserve_committed_savings
        )
        changed.append(f"reserve-committed-savings to {payload.reserve_committed_savings}")

    if payload.language is not None:
        # #10: bounded by the locales the box actually built — a language we
        # can't serve would silently fall back and confuse the family.
        if payload.language not in repository.SUPPORTED_LANGUAGES:
            supported = ", ".join(repository.SUPPORTED_LANGUAGES)
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported language; supported: {supported}",
            )
        repository.set_household_language(engine, session.household_id, payload.language)
        changed.append(f"language to {payload.language}")

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


@router.get(
    "/household/key-status",
    operation_id="getHouseholdKeyStatus",
    response_model=HouseholdKeyStatus,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Which unwrap paths exist for the household's data key (ADR 0072)",
)
async def get_household_key_status(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> HouseholdKeyStatus:
    return HouseholdKeyStatus(**household_crypto.wrap_status(engine, session.household_id))


@router.post(
    "/household/recovery-key",
    operation_id="generateRecoveryKey",
    response_model=RecoveryKey,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {
            "description": "Per-household encryption is not enabled on this box",
            "model": ErrorResponse,
        },
    },
    summary="Mint (or replace) the household recovery key — displayed exactly once",
)
async def generate_recovery_key(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> RecoveryKey:
    secret = household_crypto.generate_recovery_key(engine, session.household_id)
    if secret is None:
        raise HTTPException(
            status_code=409,
            detail="Per-household encryption is not enabled on this box (no master key).",
        )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "household.recovery_key_generated",
        "household",
        session.household_id,
        "Recovery key generated (any previous recovery key no longer works)",
    )
    return RecoveryKey(recovery_key=secret)


@router.post(
    "/household/seal-mode",
    operation_id="setSealMode",
    response_model=HouseholdKeyStatus,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {"description": "Preconditions not met", "model": ErrorResponse},
    },
    summary="Switch the household between convenient and sealed encryption modes",
)
async def set_seal_mode(
    payload: SealModeRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> HouseholdKeyStatus:
    if payload.mode == "sealed":
        error = household_crypto.seal_household(engine, session.household_id)
    else:
        error = household_crypto.unseal_household(engine, session.household_id)
    if error is not None:
        raise HTTPException(status_code=409, detail=error)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "household.seal_mode_changed",
        "household",
        session.household_id,
        f"Encryption mode set to {payload.mode}",
    )
    return HouseholdKeyStatus(**household_crypto.wrap_status(engine, session.household_id))


@router.get(
    "/household/device-wrap",
    operation_id="getDeviceWrap",
    response_model=DeviceWrap,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "No wrap for this device", "model": ErrorResponse},
    },
    summary="The calling paired device's wrap of the household data key",
)
async def get_device_wrap(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> DeviceWrap:
    if session.device_id is None:
        raise HTTPException(status_code=404, detail="This session is not device-bound")
    wrap = household_crypto.device_wrap_json(engine, session.household_id, session.device_id)
    if wrap is None:
        raise HTTPException(status_code=404, detail="No key wrap exists for this device yet")
    return DeviceWrap(wrap_json=wrap)


@router.post(
    "/household/key-session",
    operation_id="openKeySession",
    response_model=HouseholdKeyStatus,
    responses={
        400: {"description": "Key failed validation", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
    },
    summary="Unlock a sealed household with a device-unwrapped data key",
)
async def open_key_session(
    payload: KeySessionRequest,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> HouseholdKeyStatus:
    import base64 as b64

    try:
        dek = payload.dek.encode()
        # Keys are urlsafe-b64 Fernet keys already; validate decodability.
        b64.urlsafe_b64decode(dek)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Malformed key") from exc
    if not household_crypto.unlock_household(engine, session.household_id, dek):
        raise HTTPException(
            status_code=400, detail="That key does not match this household's data."
        )
    return HouseholdKeyStatus(**household_crypto.wrap_status(engine, session.household_id))


@router.post(
    "/household/recovery-unlock",
    operation_id="unlockWithRecoveryKey",
    response_model=HouseholdKeyStatus,
    responses={
        400: {"description": "The recovery key does not match", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Unlock the household with its recovery key (fresh hardware / lost passwords)",
)
async def unlock_with_recovery_key(
    payload: RecoveryUnlockRequest,
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> HouseholdKeyStatus:
    if not household_crypto.unlock_with_recovery_key(
        engine, session.household_id, payload.recovery_key.strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="That recovery key does not match this household.",
        )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "household.recovery_key_used",
        "household",
        session.household_id,
        "Household unlocked with the recovery key",
    )
    return HouseholdKeyStatus(**household_crypto.wrap_status(engine, session.household_id))


@router.get(
    "/household/export",
    operation_id="exportHousehold",
    responses={
        200: {
            "description": "A zip of the household's data (CSV + JSON + files)",
            "content": {"application/zip": {}},
        },
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        423: {"description": "Sealed household is locked", "model": ErrorResponse},
    },
    summary="Export this household's data as a portable zip (#189)",
)
async def export_household(
    session: repository.SessionContext = Depends(require_right(rights.BACKUPS_MANAGE)),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
):
    import tempfile

    from fastapi.responses import FileResponse

    from family_cfo_api import household_export

    # Not a context manager on purpose: the file must OUTLIVE this function —
    # FileResponse streams it and the BackgroundTask below unlinks it after.
    fd, out_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    counts = household_export.build_export_zip(
        engine, settings, session.household_id, out_path
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "household.exported",
        "household",
        session.household_id,
        f"Exported household data ({counts.get('transactions', 0)} transactions)",
    )
    from starlette.background import BackgroundTask

    return FileResponse(
        out_path,
        media_type="application/zip",
        filename=household_export.export_filename(),
        background=BackgroundTask(os.unlink, out_path),
    )


@router.post(
    "/savings/contributions",
    operation_id="declareSavingsContribution",
    response_model=SavingsContribution,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
        423: {"description": "Sealed household is locked", "model": ErrorResponse},
    },
    summary="Declare a recurring savings contribution (#203)",
)
async def declare_savings_contribution(
    payload: SavingsContributionCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.TRANSACTIONS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> SavingsContribution:
    from family_cfo_api import savings_detection

    source = repository.get_account(engine, session.household_id, payload.source_account_id)
    destination = repository.get_account(
        engine, session.household_id, payload.destination_account_id
    )
    if source is None or destination is None:
        raise HTTPException(status_code=404, detail="Account not found")

    record = repository.create_savings_contribution(
        engine,
        session.household_id,
        source_account_id=payload.source_account_id,
        destination_account_id=payload.destination_account_id,
        amount_minor=payload.amount.amount_minor,
        currency=payload.amount.currency,
        frequency=payload.frequency,
        source="declared",
    )
    # Declaring a route un-dismisses it: the household changed its mind.
    repository.undismiss_savings_route(
        engine, session.household_id, payload.source_account_id, payload.destination_account_id
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "savings_contribution.created",
        "savings_contribution",
        record.id,
        f"Tracking {destination.name} as a {payload.frequency} contribution",
        undo_token=undo_actions.created("savings_contribution", record.id),
    )
    monthly = savings_detection.monthly_equivalent_minor
    today = date.today()
    return SavingsContribution(
        destination_name=destination.name,
        destination_type=destination.account_type,
        amount=MoneySchema(amount_minor=record.amount_minor, currency=record.currency),
        frequency=record.frequency,
        monthly_equivalent=MoneySchema(
            amount_minor=monthly(
                savings_detection.ContributionCandidate(
                    source_account_id=record.source_account_id,
                    destination_account_id=record.destination_account_id,
                    destination_name=destination.name,
                    destination_type=destination.account_type,
                    amount_minor=record.amount_minor,
                    currency=record.currency,
                    frequency=record.frequency,
                    occurrences=0,
                    last_seen=today,
                    next_expected=today,
                )
            ),
            currency=record.currency,
        ),
        occurrences=0,
        last_seen=today,
        declared=True,
        contribution_id=record.id,
        source_account_id=record.source_account_id,
        destination_account_id=record.destination_account_id,
    )


@router.patch(
    "/savings/contributions/{contribution_id}",
    operation_id="updateSavingsContribution",
    response_model=SavingsContribution,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Contribution or goal not found", "model": ErrorResponse},
    },
    summary="Link a contribution to the goal it funds (#4)",
)
async def update_savings_contribution(
    contribution_id: str,
    payload: SavingsContributionUpdateRequest,
    session: repository.SessionContext = Depends(require_right(rights.TRANSACTIONS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> SavingsContribution:
    record = repository.get_savings_contribution(engine, session.household_id, contribution_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Contribution not found")
    goal_name: str | None = None
    if payload.goal_id is not None:
        goal = next(
            (g for g in repository.list_goals(engine, session.household_id)
             if g.id == payload.goal_id),
            None,
        )
        if goal is None:
            raise HTTPException(status_code=404, detail="Goal not found")
        goal_name = goal.name
    repository.link_savings_contribution_to_goal(
        engine, session.household_id, contribution_id, payload.goal_id
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "savings_contribution.linked" if payload.goal_id else "savings_contribution.unlinked",
        "savings_contribution",
        contribution_id,
        (f"Contribution now funds {goal_name}" if goal_name
         else "Contribution no longer funds a goal"),
        undo_token=undo_actions.savings_contribution_link_changed(record),
    )
    updated = repository.get_savings_contribution(engine, session.household_id, contribution_id)
    names = repository.account_name_map(engine, session.household_id)
    types = repository.account_type_map(engine, session.household_id)
    from family_cfo_api import savings_detection as _sd

    candidate = _sd.ContributionCandidate(
        source_account_id=updated.source_account_id,
        destination_account_id=updated.destination_account_id,
        destination_name=names.get(updated.destination_account_id, "Savings"),
        destination_type=types.get(updated.destination_account_id, "savings"),
        amount_minor=updated.amount_minor,
        currency=updated.currency,
        frequency=updated.frequency,
        occurrences=0,
        last_seen=date.today(),
        next_expected=date.today(),
    )
    return SavingsContribution(
        destination_name=candidate.destination_name,
        destination_type=candidate.destination_type,
        amount=MoneySchema(amount_minor=updated.amount_minor, currency=updated.currency),
        frequency=updated.frequency,
        monthly_equivalent=MoneySchema(
            amount_minor=_sd.monthly_equivalent_minor(candidate), currency=updated.currency
        ),
        occurrences=0,
        last_seen=date.today(),
        declared=True,
        contribution_id=updated.id,
        source_account_id=updated.source_account_id,
        destination_account_id=updated.destination_account_id,
        goal_id=updated.goal_id,
    )


@router.delete(
    "/savings/contributions/{contribution_id}",
    operation_id="deleteSavingsContribution",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Contribution not found", "model": ErrorResponse},
    },
    summary="Stop tracking a declared savings contribution",
)
async def delete_savings_contribution(
    contribution_id: str,
    session: repository.SessionContext = Depends(require_right(rights.TRANSACTIONS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    record = repository.get_savings_contribution(
        engine, session.household_id, contribution_id
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Contribution not found")
    repository.delete_savings_contribution(engine, session.household_id, contribution_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "savings_contribution.deleted",
        "savings_contribution",
        contribution_id,
        "Stopped tracking a savings contribution",
        undo_token=undo_actions.savings_contribution_deleted(record),
    )
    return Response(status_code=204)


@router.post(
    "/savings/contributions/dismiss",
    operation_id="dismissSavingsContribution",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Tell the app a detected route is not saving",
)
async def dismiss_savings_contribution(
    payload: SavingsContributionDismissRequest,
    session: repository.SessionContext = Depends(require_right(rights.TRANSACTIONS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    repository.dismiss_savings_route(
        engine,
        session.household_id,
        payload.source_account_id,
        payload.destination_account_id,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "savings_contribution.dismissed",
        "household",
        session.household_id,
        "Marked a detected transfer as not saving",
        undo_token=undo_actions.savings_route_dismissed(
            payload.source_account_id, payload.destination_account_id
        ),
    )
    return Response(status_code=204)
