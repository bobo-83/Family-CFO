from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from family_cfo_financial_engine import (
    AccountBalance,
    CalculationResult,
    DebtInput,
    FutureValueInput,
    GoalInput,
    Money,
    PurchaseImpactInputs,
    RecurringAmount,
    RetirementInput,
    calculate_cash_flow,
    calculate_debt_payoff,
    calculate_emergency_fund_months,
    calculate_future_value,
    calculate_net_worth,
    calculate_purchase_impact,
    calculate_retirement_projection,
)
from sqlalchemy.engine import Engine

from family_cfo_api import repository

LIQUID_ACCOUNT_TYPES = frozenset({"checking", "savings"})

# Spendability categories (M33; shared with ai_tools since M38): which assets
# can actually fund a purchase.
ASSET_CATEGORY_BY_TYPE = {
    "checking": "liquid",
    "savings": "liquid",
    "brokerage": "investments",
    "retirement": "retirement",
    "hsa": "retirement",
    "529": "education",
    "real_estate": "property",
    "other_asset": "property",
}
ASSET_CATEGORY_ORDER = ("liquid", "investments", "retirement", "education", "property")

# Standard emergency-fund guidance (M38): months of essential expenses.
EMERGENCY_FUND_TARGET_MIN_MONTHS = 3.0
EMERGENCY_FUND_TARGET_RECOMMENDED_MONTHS = 6.0

# How far ahead the Overview's upcoming-bills card looks (M39).
UPCOMING_BILL_WINDOW_DAYS = 14

_DAY_STEP_BY_FREQUENCY = {"weekly": 7, "biweekly": 14, "semimonthly": 15}
_MONTH_STEP_BY_FREQUENCY = {"monthly": 1, "quarterly": 3, "annual": 12}


def add_months(anchor: date, months: int) -> date:
    """Add whole months, clamping to the last valid day (Jan 31 + 1mo -> Feb 28/29)."""
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


# Backwards-compatible private alias (used by next_bill_occurrence).
_add_months = add_months


def next_bill_occurrence(next_due_date: date, frequency: str, today: date) -> date:
    """Advance a stored due date to its next occurrence on or after `today`.

    A due date already in the future is returned unchanged; a stale one is
    rolled forward by its frequency so it never shows as overdue. An unknown
    frequency (should not happen given the DB CHECK) is returned as-is.
    """
    if next_due_date >= today:
        return next_due_date

    if frequency in _DAY_STEP_BY_FREQUENCY:
        step = _DAY_STEP_BY_FREQUENCY[frequency]
        gap = (today - next_due_date).days
        return next_due_date + timedelta(days=((gap + step - 1) // step) * step)

    if frequency in _MONTH_STEP_BY_FREQUENCY:
        step = _MONTH_STEP_BY_FREQUENCY[frequency]
        occurrence = next_due_date
        while occurrence < today:
            occurrence = _add_months(occurrence, step)
        return occurrence

    return next_due_date


@dataclass(frozen=True, slots=True)
class UpcomingBill:
    id: str
    name: str
    amount: Money
    due_date: date
    days_until: int


def upcoming_bills(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> list[UpcomingBill]:
    """Bills whose next occurrence falls within UPCOMING_BILL_WINDOW_DAYS, soonest first."""
    today = today or date.today()
    horizon = today + timedelta(days=UPCOMING_BILL_WINDOW_DAYS)
    result: list[UpcomingBill] = []
    for bill in repository.list_bills(engine, household_id):
        if bill.next_due_date is None:
            continue
        due = next_bill_occurrence(bill.next_due_date, bill.frequency, today)
        if due <= horizon:
            result.append(
                UpcomingBill(
                    id=bill.id,
                    name=bill.name,
                    amount=Money(bill.amount_minor, bill.currency),
                    due_date=due,
                    days_until=(due - today).days,
                )
            )
    result.sort(key=lambda b: b.due_date)
    return result


@dataclass(frozen=True, slots=True)
class EmergencyFundInputs:
    """The fund balance the coverage calculation measures, and its provenance."""

    fund: Money
    using_designations: bool
    monthly_bills: Money


def emergency_fund_inputs(engine: Engine, household_id: str, currency: str) -> EmergencyFundInputs:
    balances = repository.list_account_balances(engine, household_id)
    liquid_balance = Money.zero(currency)
    designated_minor = 0
    for balance in balances:
        if balance.account_type in LIQUID_ACCOUNT_TYPES:
            liquid_balance += Money(balance.balance_minor, balance.currency)
        # M36: user-designated reservations, on any account type.
        if balance.currency == currency:
            designated_minor += repository.emergency_fund_reserved_minor(
                balance.emergency_fund_percent, balance.emergency_fund_minor, balance.balance_minor
            )
    using_designations = designated_minor > 0
    # M36: once the family designates emergency-fund money, coverage measures
    # that fund — not the legacy "all liquid money" approximation.
    fund = Money(designated_minor, currency) if using_designations else liquid_balance
    return EmergencyFundInputs(fund, using_designations, _monthly_bill_total(engine, household_id, currency))


@dataclass(frozen=True, slots=True)
class DebtOutlook:
    """Aggregated debt-payoff outlook across the household's liability accounts with terms."""

    modeled_count: int
    unmodeled_count: int
    total_interest_remaining: Money | None
    longest_months: int | None
    calculation_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def compute_debt_outlook(engine: Engine, household_id: str, currency: str) -> DebtOutlook:
    """Run calculate_debt_payoff over each liability account that carries terms.

    Only debts in the household base currency are aggregated (the app is
    single-currency per household); a debt in another currency, or one whose
    payment never clears the balance, is surfaced as unmodeled/warned rather
    than folded into a misleading total.
    """
    debts = repository.list_debts_with_terms(engine, household_id)
    unmodeled = repository.count_liabilities_without_terms(engine, household_id)

    refs: list[str] = []
    warnings: list[str] = []
    total_interest = Money.zero(currency)
    total_known = True
    longest = 0
    modeled = 0

    for debt in debts:
        if debt.currency != currency:
            unmodeled += 1
            continue
        result = calculate_debt_payoff(
            DebtInput(
                debt_id=debt.account_id,
                name=debt.name,
                balance=Money(debt.balance_owed_minor, debt.currency),
                annual_interest_rate=debt.annual_interest_rate,
                minimum_payment=Money(debt.minimum_payment_minor, debt.currency),
            )
        )
        calc_id = _persist(engine, household_id, result)
        refs.append(f"financial_calculations:{calc_id}")
        modeled += 1
        months = result.outputs["months_to_payoff"]
        interest = result.outputs["total_interest_paid"]
        if months is None or interest is None:
            total_known = False
            warnings.extend(result.warnings)
        else:
            longest = max(longest, months)
            total_interest += interest

    return DebtOutlook(
        modeled_count=modeled,
        unmodeled_count=unmodeled,
        total_interest_remaining=total_interest if (modeled and total_known) else None,
        longest_months=longest if (modeled and total_known) else None,
        calculation_refs=refs,
        warnings=warnings,
    )


def compute_retirement_projection(
    engine: Engine, household_id: str, inputs: RetirementInput
) -> tuple[CalculationResult, str]:
    result = calculate_retirement_projection(inputs)
    calculation_id = _persist(engine, household_id, result)
    return result, calculation_id


def compute_future_value(
    engine: Engine, household_id: str, inputs: FutureValueInput
) -> tuple[CalculationResult, str]:
    result = calculate_future_value(inputs)
    calculation_id = _persist(engine, household_id, result)
    return result, calculation_id


def compute_debt_payoff(
    engine: Engine, household_id: str, debt: DebtInput
) -> tuple[CalculationResult, str]:
    result = calculate_debt_payoff(debt)
    calculation_id = _persist(engine, household_id, result)
    return result, calculation_id


def compute_net_worth(engine: Engine, household_id: str, currency: str) -> CalculationResult:
    result, _calculation_id = compute_net_worth_with_ref(engine, household_id, currency)
    return result


def compute_net_worth_with_ref(
    engine: Engine, household_id: str, currency: str
) -> tuple[CalculationResult, str]:
    balances = repository.list_account_balances(engine, household_id)
    engine_balances = [
        AccountBalance(b.account_id, b.account_type, Money(b.balance_minor, b.currency))
        for b in balances
    ]

    result = calculate_net_worth(engine_balances, currency)
    calculation_id = _persist(engine, household_id, result)
    return result, calculation_id


def compute_emergency_fund(engine: Engine, household_id: str, currency: str) -> CalculationResult:
    result, _calculation_id = compute_emergency_fund_with_ref(engine, household_id, currency)
    return result


def compute_emergency_fund_with_ref(
    engine: Engine, household_id: str, currency: str
) -> tuple[CalculationResult, str]:
    inputs = emergency_fund_inputs(engine, household_id, currency)
    result = calculate_emergency_fund_months(inputs.fund, inputs.monthly_bills)
    calculation_id = _persist(engine, household_id, result)
    return result, calculation_id


def compute_purchase_impact(
    engine: Engine, household_id: str, currency: str, price: Money
) -> tuple[CalculationResult, str]:
    balances = repository.list_account_balances(engine, household_id)
    engine_balances = [
        AccountBalance(b.account_id, b.account_type, Money(b.balance_minor, b.currency))
        for b in balances
    ]
    net_worth_result = calculate_net_worth(engine_balances, currency)

    liquid_balance = Money.zero(currency)
    for balance in balances:
        if balance.account_type in LIQUID_ACCOUNT_TYPES:
            liquid_balance += Money(balance.balance_minor, balance.currency)

    income_amounts = [
        RecurringAmount(income.name, Money(income.amount_minor, income.currency), income.frequency)
        for income in repository.list_income_sources(engine, household_id)
    ]
    bill_amounts = [
        RecurringAmount(bill.name, Money(bill.amount_minor, bill.currency), bill.frequency)
        for bill in repository.list_bills(engine, household_id)
    ]
    cash_flow_result = calculate_cash_flow(
        income_amounts, bill_amounts, Money.zero(currency), currency
    )

    goals = repository.list_goals(engine, household_id)
    top_goal = None
    if goals:
        top = goals[0]
        top_goal = GoalInput(
            goal_id=top.id,
            name=top.name,
            target=Money(top.target_minor, top.currency),
            current=Money(top.current_minor, top.currency),
        )

    result = calculate_purchase_impact(
        PurchaseImpactInputs(
            price=price,
            net_worth_before=net_worth_result.outputs["net_worth"],
            liquid_balance_before=liquid_balance,
            monthly_essential_expenses=cash_flow_result.outputs["monthly_bills"],
            discretionary_cash_flow=cash_flow_result.outputs["discretionary_cash_flow"],
            liability_total=net_worth_result.outputs["liability_total"],
            top_goal=top_goal,
        )
    )
    calculation_id = _persist(engine, household_id, result)
    return result, calculation_id


def monthly_income_total(engine: Engine, household_id: str, currency: str) -> Money:
    total = Money.zero(currency)
    for income in repository.list_income_sources(engine, household_id):
        recurring = RecurringAmount(
            income.name, Money(income.amount_minor, income.currency), income.frequency
        )
        total += recurring.monthly_amount()
    return total


def monthly_bill_total(engine: Engine, household_id: str, currency: str) -> Money:
    return _monthly_bill_total(engine, household_id, currency)


def _monthly_bill_total(engine: Engine, household_id: str, currency: str) -> Money:
    bills = repository.list_bills(engine, household_id)
    total = Money.zero(currency)
    for bill in bills:
        recurring = RecurringAmount(
            bill.name, Money(bill.amount_minor, bill.currency), bill.frequency
        )
        total += recurring.monthly_amount()

    return total


def _serialize_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    def serialize(value: Any) -> Any:
        if isinstance(value, Money):
            return value.to_dict()
        if isinstance(value, dict):
            return {key: serialize(item) for key, item in value.items()}
        return value

    return {key: serialize(value) for key, value in outputs.items()}


def _persist(engine: Engine, household_id: str, result: CalculationResult) -> str:
    return repository.record_calculation(
        engine,
        household_id=household_id,
        calculation_type=result.calculation_type,
        version=result.version,
        inputs=result.inputs,
        assumptions=result.assumptions,
        warnings=result.warnings,
        outputs=_serialize_outputs(result.outputs),
    )
