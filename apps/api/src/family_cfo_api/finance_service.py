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
    SafeToSpendInputs,
    calculate_cash_flow,
    calculate_debt_payoff,
    calculate_emergency_fund_months,
    calculate_future_value,
    calculate_net_worth,
    calculate_purchase_impact,
    calculate_retirement_projection,
    calculate_safe_to_spend,
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
    engine: Engine,
    household_id: str,
    currency: str,
    *,
    today: date | None = None,
    window_days: int | None = None,
) -> list[UpcomingBill]:
    """Bills whose next occurrence falls within the window, soonest first.

    Defaults to UPCOMING_BILL_WINDOW_DAYS (the Overview's "due soon" horizon);
    safe-to-spend passes a longer one, because money is committed to a bill from
    the moment you know it is coming, not when it becomes imminent.
    """
    today = today or date.today()
    horizon = today + timedelta(days=window_days or UPCOMING_BILL_WINDOW_DAYS)
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
    """The fund balance the coverage calculation measures, and its provenance.

    The monthly-need denominator is computed separately by
    ``monthly_essential_expenses`` — it runs a trailing-spending query and a debt
    sweep, so the callers that only want the fund balance (safe-to-spend, goal
    current) don't pay for it."""

    fund: Money
    using_designations: bool


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
    return EmergencyFundInputs(fund, using_designations)


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


def reconstruct_net_worth(
    engine: Engine, household_id: str, as_of: date, currency: str
) -> int:
    """Net worth at the end of a past month, reconstructed from today's balances
    minus every transaction that has posted since. Approximate (it can't rewind
    market moves on investments), but far better than nothing for a month before
    daily net-worth snapshots existed. Returns the net-worth amount in minor units."""
    balances = repository.list_account_balances(engine, household_id)
    later = repository.list_transactions(
        engine, household_id, limit=1_000_000, start=as_of + timedelta(days=1)
    )
    posted_since: dict[str, int] = {}
    for txn in later:
        posted_since[txn.account_id] = posted_since.get(txn.account_id, 0) + txn.amount_minor

    engine_balances = [
        AccountBalance(
            b.account_id,
            b.account_type,
            Money(b.balance_minor - posted_since.get(b.account_id, 0), b.currency),
        )
        for b in balances
        if b.currency == currency
    ]
    result = calculate_net_worth(engine_balances, currency)
    return int(result.outputs["net_worth"].amount_minor)


def reconstruct_debt_total(engine: Engine, household_id: str, as_of: date, currency: str) -> int:
    """Total owed across all liability accounts at the end of a past date,
    reconstructed from today's balances minus the liability transactions posted
    since (mirrors reconstruct_net_worth). Approximate — a mortgage whose balance
    is synced rather than transacted won't rewind perfectly — but it is the only
    debt history that exists before the daily balance record began. Returns a
    positive amount owed in minor units."""
    balances = repository.list_account_balances(engine, household_id)
    liability_ids = {
        b.account_id for b in balances if b.account_type in repository.LIABILITY_ACCOUNT_TYPES
    }
    later = repository.list_transactions(
        engine, household_id, limit=1_000_000, start=as_of + timedelta(days=1)
    )
    posted_since: dict[str, int] = {}
    for txn in later:
        if txn.account_id in liability_ids:
            posted_since[txn.account_id] = posted_since.get(txn.account_id, 0) + txn.amount_minor

    total_owed = 0
    for b in balances:
        if b.account_type in repository.LIABILITY_ACCOUNT_TYPES and b.currency == currency:
            reconstructed = b.balance_minor - posted_since.get(b.account_id, 0)
            total_owed += max(0, -reconstructed)  # a liability is a negative balance
    return total_owed


@dataclass(frozen=True, slots=True)
class DebtHistoryPoint:
    month: str  # YYYY-MM
    total_owed: Money


@dataclass(frozen=True, slots=True)
class DebtHistory:
    points: list[DebtHistoryPoint]
    average: Money
    months_covered: int


def debt_history(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> DebtHistory:
    """Total debt at each month-end across the household's transaction history,
    plus the average over that window (ADR 0043). Reconstructed month by month;
    'lifetime' is bounded by how much history exists."""
    today = today or date.today()
    earliest = repository.earliest_transaction_month(engine, household_id)
    points: list[DebtHistoryPoint] = []
    if earliest is not None:
        cursor = date(int(earliest[:4]), int(earliest[5:7]), 1)
        current_month_start = today.replace(day=1)
        while cursor <= current_month_start:
            # Month-end, except the current (partial) month uses today.
            as_of = today if cursor == current_month_start else add_months(cursor, 1) - timedelta(days=1)
            owed = reconstruct_debt_total(engine, household_id, as_of, currency)
            points.append(DebtHistoryPoint(f"{cursor.year}-{cursor.month:02d}", Money(owed, currency)))
            cursor = add_months(cursor, 1)
    average_minor = (
        round(sum(p.total_owed.amount_minor for p in points) / len(points)) if points else 0
    )
    return DebtHistory(points, Money(average_minor, currency), len(points))


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


def compute_emergency_fund(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> CalculationResult:
    result, _calculation_id = compute_emergency_fund_with_ref(
        engine, household_id, currency, today=today
    )
    return result


def compute_emergency_fund_with_ref(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> tuple[CalculationResult, str]:
    inputs = emergency_fund_inputs(engine, household_id, currency)
    expenses = monthly_essential_expenses(engine, household_id, currency, today=today)
    result = calculate_emergency_fund_months(inputs.fund, expenses)
    calculation_id = _persist(engine, household_id, result)
    return result, calculation_id


SAFE_TO_SPEND_HORIZON_DAYS = 30


@dataclass(frozen=True, slots=True)
class LiabilityObligation:
    """A recurring monthly payment on a liability account (M106) — a mortgage/loan
    payment, a lease payment, or a payroll-deducted 401(k)-loan repayment. Shown on
    the Bills tab alongside actual bills, and (when ``reserved``) subtracted from
    safe-to-spend. ``note`` explains what the payment does to the balance sheet."""

    account_id: str
    name: str
    amount_minor: int
    currency: str
    kind: str  # "mortgage" | "loan" | "lease" | "retirement_loan"
    note: str
    reserved: bool
    next_payment_due_date: date | None = None  # ADR 0033: from a statement or set by hand


def recurring_liability_obligations(
    engine: Engine, household_id: str, currency: str
) -> list[LiabilityObligation]:
    """Every liability account carrying a recorded monthly payment, classified by
    what the payment does to the balance sheet. A liability with a balance owed is a
    LOAN (the payment pays down principal + interest); one with a payment but no
    balance to pay down is a LEASE (a pure expense, like rent). Credit cards are
    excluded — their whole balance is handled by the cards line, not a minimum
    here."""
    balances = {
        b.account_id: b for b in repository.list_account_balances(engine, household_id)
    }
    liability_accounts = repository.list_liability_accounts(engine, household_id)
    # A liability that is ALSO set up as an explicit bill is shown once — as the
    # bill, which carries the due date and matches the real charge (ADR 0032).
    covered = bill_covered_account_ids(
        repository.list_bills(engine, household_id), liability_accounts
    )
    obligations: list[LiabilityObligation] = []
    for account in liability_accounts:
        if account.currency != currency or account.minimum_payment_minor is None:
            continue
        if account.account_type == "credit_card":
            continue
        if account.id in covered:
            continue
        balance = balances.get(account.id)
        owes = balance is not None and balance.balance_minor < 0
        if account.account_type in repository.RETIREMENT_LOAN_TYPES:
            kind, reserved = "retirement_loan", False
            note = (
                "Repaid by payroll deduction — already reflected in your take-home "
                "pay, so it isn't reserved again from your cash."
            )
        elif account.account_type == "mortgage":
            kind, reserved = "mortgage", True
            note = (
                "Pays down your mortgage principal plus interest — the principal "
                "portion builds home equity. Reserved in safe-to-spend."
            )
        elif owes:
            kind, reserved = "loan", True
            note = (
                f"Pays down what you owe on {account.name} (principal) plus "
                "interest, lowering your debt. Reserved in safe-to-spend."
            )
        else:
            kind, reserved = "lease", True
            note = (
                "A lease payment — a monthly expense that builds no equity (you "
                "don't own it), like rent. Reserved in safe-to-spend."
            )
        obligations.append(
            LiabilityObligation(
                account_id=account.id,
                name=account.name,
                amount_minor=account.minimum_payment_minor,
                currency=account.currency,
                kind=kind,
                note=note,
                reserved=reserved,
                next_payment_due_date=account.next_payment_due_date,
            )
        )
    return obligations


# --- Recurring income detection (shared) & the 30-day cash outlook (M112) ----


def recurring_income_candidates(
    engine: Engine, household_id: str, *, since: date
) -> tuple[list, list, set[str], set[str]]:
    """The income-analysis detection pipeline (M61–M63), reusable: inflows with
    internal transfers dropped, user overrides applied, grouped into recurring
    sources. Returns (transactions, candidates, included_ids, excluded_ids) —
    the single source for both the income analysis and the cash outlook."""
    from family_cfo_api import income_detection

    rows = repository.list_income_detection_transactions(engine, household_id, since=since)
    transactions = [
        income_detection.IncomeTransaction(
            id=txn_id,
            occurred_at=occurred_at,
            amount_minor=amount_minor,
            currency=currency,
            merchant=merchant,
            description=description,
            account_name=account_name,
        )
        for (txn_id, occurred_at, amount_minor, currency, merchant, description, account_name)
        in rows
    ]
    overrides = repository.list_income_overrides(engine, household_id)
    excluded_ids = {txn_id for txn_id, verdict in overrides.items() if verdict == "exclude"}
    included_ids = {txn_id for txn_id, verdict in overrides.items() if verdict == "include"}

    # M63: internal transfers (the household's own money changing accounts) are
    # not income. An explicit "include" verdict overrides.
    outflows_by_amount: dict[int, list[date]] = {}
    for occurred_at, amount_minor in repository.list_household_outflows(
        engine, household_id, since=since
    ):
        outflows_by_amount.setdefault(amount_minor, []).append(occurred_at)
    transactions = [
        t
        for t in transactions
        if t.id in included_ids
        or not income_detection.is_internal_transfer(t, outflows_by_amount)
    ]
    candidates = income_detection.detect_income_sources(transactions, excluded_ids=excluded_ids)
    return transactions, candidates, included_ids, excluded_ids


CASH_OUTLOOK_HORIZON_DAYS = 30
_INCOME_DETECTION_WINDOW_DAYS = 365


@dataclass(frozen=True, slots=True)
class OutlookEvent:
    """One expected cash movement in the outlook window: a payday (positive) or
    a payment (negative)."""

    occurred_on: date
    name: str
    amount_minor: int  # signed: inflow positive, outflow negative
    kind: str  # "income" | "bill" | "credit_card" | "mortgage" | "loan" | "lease"


@dataclass(frozen=True, slots=True)
class CashOutlook:
    starting_cash_minor: int
    events: list[OutlookEvent]  # date order; same-day outflows before inflows
    ending_cash_minor: int
    lowest_minor: int
    lowest_date: date | None
    expected_income_minor: int
    obligations_minor: int
    horizon_days: int


def _step(anchor: date, frequency: str) -> date:
    if frequency in _DAY_STEP_BY_FREQUENCY:
        return anchor + timedelta(days=_DAY_STEP_BY_FREQUENCY[frequency])
    return add_months(anchor, _MONTH_STEP_BY_FREQUENCY.get(frequency, 1))


def cash_outlook(
    engine: Engine,
    household_id: str,
    currency: str,
    *,
    today: date | None = None,
    horizon_days: int = CASH_OUTLOOK_HORIZON_DAYS,
) -> CashOutlook:
    """Projected cash over the horizon (M112, ADR 0026): starting liquid cash,
    each expected payment (from the payment timeline) and each expected payday
    (from recurring-income detection), and the lowest point the balance reaches.

    Unlike safe-to-spend — a zero-income stress test — this is the lived
    question: "given my paychecks and my payments, where does cash actually
    go?" Same-day outflows apply before inflows, so the lowest point errs low.
    """
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)

    # --- Outflows: the payment timeline's items, projected over the window.
    timeline = payment_timeline(
        engine, household_id, currency, today=today, window_days=horizon_days
    )
    bill_frequency = {
        bill.id: bill.frequency for bill in repository.list_bills(engine, household_id)
    }
    events: list[OutlookEvent] = []
    for item in timeline.items:
        if item.due_date is None:
            continue  # undated card: nothing to place on the calendar
        # Overdue items claim cash immediately; everything else on its due date.
        first = today if item.status == "overdue" else item.due_date
        if first > horizon:
            continue
        events.append(
            OutlookEvent(
                occurred_on=first, name=item.name,
                amount_minor=-item.amount_minor, kind=item.kind,
            )
        )
        # Sub-monthly bills and fixed loan/lease payments recur within 30 days;
        # a card's NEXT-next statement amount is unknowable — never projected.
        if item.kind == "credit_card":
            continue
        frequency = bill_frequency.get(item.id, "monthly") if item.kind == "bill" else "monthly"
        # Future occurrences anchor on the item's own due-date cadence — an
        # overdue charge claimed "today" must not shift the future schedule.
        occurrence = _step(item.due_date, frequency)
        while occurrence <= today:
            occurrence = _step(occurrence, frequency)
        while occurrence <= horizon:
            events.append(
                OutlookEvent(
                    occurred_on=occurrence, name=item.name,
                    amount_minor=-item.amount_minor, kind=item.kind,
                )
            )
            occurrence = _step(occurrence, frequency)

    # --- Inflows: recurring income sources, stepped forward from their last
    # sighting. Detection needs 2–3 consistent sightings, so a brand-new job
    # won't project until it has history — honest, if conservative.
    since = today - timedelta(days=_INCOME_DETECTION_WINDOW_DAYS)
    _, candidates, _, _ = recurring_income_candidates(engine, household_id, since=since)
    for candidate in candidates:
        if candidate.currency != currency or not candidate.transactions:
            continue
        payday = max(t.occurred_at for t in candidate.transactions)
        while payday <= today:  # roll forward to the first FUTURE payday
            payday = _step(payday, candidate.frequency)
        while payday <= horizon:
            events.append(
                OutlookEvent(
                    occurred_on=payday,
                    name=candidate.name,
                    amount_minor=candidate.typical_amount_minor,
                    kind="income",
                )
            )
            payday = _step(payday, candidate.frequency)

    # Outflows before inflows on the same day, so the lowest point errs low.
    events.sort(key=lambda e: (e.occurred_on, e.amount_minor >= 0))

    starting = timeline.liquid_minor
    running = starting
    lowest = starting
    lowest_date: date | None = None
    for event in events:
        running += event.amount_minor
        if running < lowest:
            lowest = running
            lowest_date = event.occurred_on
    return CashOutlook(
        starting_cash_minor=starting,
        events=events,
        ending_cash_minor=running,
        lowest_minor=lowest,
        lowest_date=lowest_date,
        expected_income_minor=sum(e.amount_minor for e in events if e.amount_minor > 0),
        obligations_minor=-sum(e.amount_minor for e in events if e.amount_minor < 0),
        horizon_days=horizon_days,
    )


# --- The month's spending plan: "left to spend" (M113, ADR 0027) ------------


@dataclass(frozen=True, slots=True)
class SpendingPlan:
    """The Simplifi-style month plan: expected income minus what's already
    spent and what's still committed = left to spend this month."""

    month: str  # "YYYY-MM"
    income_received_minor: int  # income deposits that landed this month
    income_projected_minor: int  # paydays still expected before month end
    expected_income_minor: int  # received + projected
    spent_minor: int  # month-to-date spending (bills paid, card charges, the lot)
    bills_remaining_minor: int  # bills due (unpaid) through month end
    account_obligations_minor: int  # mortgage/loan/lease payments for the month
    planned_savings_minor: int  # goals' declared monthly contributions (M118)
    left_minor: int
    per_day_minor: int  # a pace, not a rule: left / days remaining (0 when negative)
    days_remaining: int  # including today


def spending_plan(
    engine: Engine,
    household_id: str,
    currency: str,
    *,
    today: date | None = None,
) -> SpendingPlan:
    """Left to spend this month (M113, ADR 0027):

        expected income − spent so far − bills still due − loan/lease payments

    Accrual view, complementing the cash outlook's cash-timing view. The terms
    are constructed so nothing is counted twice:

    - ``spent`` is ``sum_spending`` month-to-date: card CHARGES count when they
      happen; card PAYMENTS and loan/lease payment legs are categorized as
      Transfers and excluded. A bill paid this month is a categorized charge,
      so it lives here — and is therefore absent from ``bills_remaining``.
    - ``bills_remaining`` is the payment timeline's UNPAID bill-kind items due
      through month end (an overdue bill still claims this month's income).
    - ``account_obligations`` are the recorded monthly mortgage/loan/lease
      payments — invisible to ``sum_spending`` (transfer legs), so they are
      counted here exactly once. Cards are excluded entirely (their charges
      already counted); payroll-deducted 401(k) loans never touch deposits.
    - Income = deposits the income analysis counts, received this month, plus
      recurring paydays projected through month end.
    """
    today = today or date.today()
    month_start = today.replace(day=1)
    month_end = add_months(month_start, 1) - timedelta(days=1)

    # --- Income: received this month + projected through month end.
    since = today - timedelta(days=_INCOME_DETECTION_WINDOW_DAYS)
    transactions, candidates, included_ids, excluded_ids = recurring_income_candidates(
        engine, household_id, since=since
    )
    counted_ids = {t.id for c in candidates for t in c.transactions} | included_ids
    received = sum(
        t.amount_minor
        for t in transactions
        if month_start <= t.occurred_at <= today
        and t.currency == currency
        and t.id in counted_ids
        and t.id not in excluded_ids
    )
    projected = 0
    for candidate in candidates:
        if candidate.currency != currency or not candidate.transactions:
            continue
        payday = max(t.occurred_at for t in candidate.transactions)
        while payday <= today:
            payday = _step(payday, candidate.frequency)
        while payday <= month_end:
            projected += candidate.typical_amount_minor
            payday = _step(payday, candidate.frequency)

    # --- Already out: month-to-date spending (see docstring for what counts).
    spent = repository.sum_spending(engine, household_id, month_start, today, currency)

    # --- Still committed: unpaid bills due through month end...
    timeline = payment_timeline(
        engine, household_id, currency,
        today=today, window_days=max((month_end - today).days, 0),
    )
    bills_remaining = sum(
        item.amount_minor
        for item in timeline.items
        if item.kind == "bill"
        and item.status in ("overdue", "due_soon", "upcoming")
        and item.due_date is not None
        and item.due_date <= month_end
    )
    # ...plus the month's account-based payments (never in sum_spending).
    account_obligations = sum(
        obligation.amount_minor
        for obligation in recurring_liability_obligations(engine, household_id, currency)
        if obligation.kind != "retirement_loan"
    )
    # M118: goals' declared monthly contributions. Savings transfers are
    # Transfers (excluded from spending) and stay within liquid, so this is the
    # only place the plan reserves them — exactly once.
    planned_savings = sum(
        goal.monthly_contribution_minor or 0
        for goal in repository.list_goals(engine, household_id)
        if goal.currency == currency
    )

    expected_income = received + projected
    left = expected_income - spent - bills_remaining - account_obligations - planned_savings
    days_remaining = (month_end - today).days + 1
    return SpendingPlan(
        month=f"{today.year}-{today.month:02d}",
        income_received_minor=received,
        income_projected_minor=projected,
        expected_income_minor=expected_income,
        spent_minor=spent,
        bills_remaining_minor=bills_remaining,
        account_obligations_minor=account_obligations,
        planned_savings_minor=planned_savings,
        left_minor=left,
        per_day_minor=left // days_remaining if left > 0 else 0,
        days_remaining=days_remaining,
    )


# --- Payment timeline (M111, ADR 0024) --------------------------------------
#
# The Bills tab's primary view: every payment that will pull money out of
# checking — bills, credit-card payments, loan/lease payments — as one list
# organized by TIME (overdue / due soon / upcoming / paid this cycle), with a
# cash-versus-due headline. The unit is the payment, not the merchant.

PAYMENT_TIMELINE_WINDOW_DAYS = 14
# How far back to scan for payments when matching and inferring due days.
_TIMELINE_LOOKBACK_DAYS = 120
# A liability-account inflow counts as a payment only when its label says so —
# refunds and statement credits are also inflows and must not be mistaken for one.
_PAYMENT_LABEL_WORDS = ("payment", "autopay", "auto pay", "epay", "pymt", "paid")
# How long after a due date a bill stays "overdue" before we assume the charge
# simply isn't visible to us and stop claiming it's late.
_GRACE_DAYS_BY_FREQUENCY = {
    "weekly": 4, "biweekly": 6, "semimonthly": 6,
    "monthly": 10, "quarterly": 15, "annual": 20,
}
# Utility-style bills vary in amount around a fixed due day (ADR 0024): match the
# actual charge by merchant + window, with the same generous tolerance detection
# uses, and report the ACTUAL amount on the paid row.
_MATCH_AMOUNT_TOLERANCE = 0.30


@dataclass(frozen=True, slots=True)
class TimelinePayment:
    """The real transaction that satisfied a timeline item — the receipt behind
    the checkmark, always shown so a 'Paid' claim can be verified."""

    transaction_id: str
    occurred_at: date
    amount_minor: int  # positive: what was actually paid/charged
    label: str


@dataclass(frozen=True, slots=True)
class PaymentTimelineItem:
    id: str  # bill id, or liability account id
    kind: str  # "bill" | "credit_card" | "mortgage" | "loan" | "lease"
    name: str
    amount_minor: int  # expected: bill estimate / card balance / minimum payment
    currency: str
    due_date: date | None  # None = we couldn't infer one ("no_date")
    status: str  # "overdue" | "due_soon" | "upcoming" | "paid" | "no_date"
    paid: TimelinePayment | None


@dataclass(frozen=True, slots=True)
class PaymentTimeline:
    items: list[PaymentTimelineItem]
    due_total_minor: int  # overdue + due-soon, the "what needs paying now" number
    liquid_minor: int
    covered: bool
    window_days: int


def _keys_match(a: str, b: str) -> bool:
    """Merchant-key match, slightly fuzzy: 'department of education' should match
    'dept of education'. Equal, substring, or token-subset counts; anything looser
    risks a false 'Paid', which is worse than no status (ADR 0024)."""
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    return ta <= tb or tb <= ta


def bill_covered_account_ids(
    bills: "list[repository.RecurringRecord]",
    liability_accounts: "list[repository.AccountRecord]",
) -> set[str]:
    """Liability accounts whose recurring payment is ALSO modeled as an explicit
    bill — same merchant (fuzzy) and amount within tolerance (ADR 0032).

    A student loan added as an account AND set up as a bill is one obligation, not
    two. The bill is authoritative for the payment (it carries a stored due date
    and matches the real charge), so the derived account obligation is suppressed
    everywhere bills are shown or reserved, and the debt is counted once. The
    account itself is untouched — it still lives in Accounts / Debts for payoff."""
    from family_cfo_api import bill_detection

    bill_keys = [(b, bill_detection.normalize_merchant(b.name)) for b in bills]
    covered: set[str] = set()
    for account in liability_accounts:
        if account.minimum_payment_minor is None:
            continue
        account_key = bill_detection.normalize_merchant(account.name)
        if not account_key:
            continue
        for bill, bill_key in bill_keys:
            if bill.currency != account.currency:
                continue
            if not _keys_match(account_key, bill_key):
                continue
            if abs(bill.amount_minor - account.minimum_payment_minor) > (
                account.minimum_payment_minor * _MATCH_AMOUNT_TOLERANCE
            ):
                continue
            covered.add(account.id)
            break
    return covered


def _previous_occurrence(due: date, frequency: str) -> date:
    if frequency in _DAY_STEP_BY_FREQUENCY:
        return due - timedelta(days=_DAY_STEP_BY_FREQUENCY[frequency])
    return add_months(due, -_MONTH_STEP_BY_FREQUENCY.get(frequency, 1))


def _find_bill_payment(
    bill: "repository.RecurringRecord",
    outflows: list["repository.TransactionRecord"],
    window_start: date,
    window_end: date,
) -> TimelinePayment | None:
    from family_cfo_api import bill_detection

    bill_key = bill_detection.normalize_merchant(bill.name)
    for txn in outflows:  # sorted most recent first; take the latest match
        if not (window_start <= txn.occurred_at <= window_end):
            continue
        txn_key = bill_detection.normalize_merchant(
            txn.merchant
        ) or bill_detection.normalize_merchant(txn.description)
        if not _keys_match(bill_key, txn_key):
            continue
        actual = abs(txn.amount_minor)
        if abs(actual - bill.amount_minor) > bill.amount_minor * _MATCH_AMOUNT_TOLERANCE:
            continue
        return TimelinePayment(
            transaction_id=txn.id,
            occurred_at=txn.occurred_at,
            amount_minor=actual,
            label=txn.merchant or txn.description or bill.name,
        )
    return None


def _account_payments(
    transactions: list["repository.TransactionRecord"],
) -> list[TimelinePayment]:
    """Payment-labeled inflows on a liability account, most recent first."""
    payments = [
        TimelinePayment(
            transaction_id=txn.id,
            occurred_at=txn.occurred_at,
            amount_minor=txn.amount_minor,
            label=txn.merchant or txn.description or "Payment",
        )
        for txn in transactions
        if txn.amount_minor > 0
        and any(
            word in (txn.merchant or txn.description or "").lower()
            for word in _PAYMENT_LABEL_WORDS
        )
    ]
    payments.sort(key=lambda p: p.occurred_at, reverse=True)
    return payments


_TIMELINE_STATUS_ORDER = {"overdue": 0, "due_soon": 1, "no_date": 2, "paid": 3, "upcoming": 4}


def payment_timeline(
    engine: Engine,
    household_id: str,
    currency: str,
    *,
    today: date | None = None,
    window_days: int = PAYMENT_TIMELINE_WINDOW_DAYS,
) -> PaymentTimeline:
    """Everything that needs paying, as one time-ordered list (M111, ADR 0024).

    Bills match their actual charges by merchant + due-window (amount within
    ±30%, so variable utilities on a fixed due day still match); credit cards and
    loans/leases infer their payment day from payment-labeled inflows on their own
    account. Inferred dates are never flagged overdue — inference isn't strong
    enough evidence to accuse anyone of missing a payment.
    """
    today = today or date.today()
    lookback_start = today - timedelta(days=_TIMELINE_LOOKBACK_DAYS)
    transactions = repository.list_transactions(
        engine, household_id, limit=100_000, start=lookback_start, end=today
    )
    by_account: dict[str, list[repository.TransactionRecord]] = {}
    outflows: list[repository.TransactionRecord] = []
    for txn in transactions:
        by_account.setdefault(txn.account_id, []).append(txn)
        if txn.amount_minor < 0:
            outflows.append(txn)
    outflows.sort(key=lambda t: t.occurred_at, reverse=True)

    horizon = today + timedelta(days=window_days)
    items: list[PaymentTimelineItem] = []

    # --- Bills: the stored due date is authoritative; match the actual charge.
    for bill in repository.list_bills(engine, household_id):
        if bill.currency != currency or bill.next_due_date is None:
            continue
        next_due = next_bill_occurrence(bill.next_due_date, bill.frequency, today)
        prev_due = _previous_occurrence(next_due, bill.frequency)
        grace = _GRACE_DAYS_BY_FREQUENCY.get(bill.frequency, 10)

        # A payment only counts for the occurrence it sits next to — matching
        # across a whole cycle would let last month's charge mark tomorrow's as
        # paid, a false checkmark (ADR 0024).
        paid: TimelinePayment | None = None
        status: str
        due: date | None
        if prev_due >= today - timedelta(days=grace):
            # A due date just passed: paid near it, or genuinely overdue.
            paid = _find_bill_payment(
                bill, outflows, prev_due - timedelta(days=5),
                min(prev_due + timedelta(days=grace), today),
            )
            if paid is not None:
                status, due = "paid", next_due
            else:
                status, due = "overdue", prev_due
        else:
            # Otherwise the question is the upcoming occurrence — possibly
            # already settled early by autopay.
            paid = _find_bill_payment(
                bill, outflows, next_due - timedelta(days=5), today
            )
            if paid is not None:
                status, due = "paid", next_due
            elif next_due <= horizon:
                status, due = "due_soon", next_due
            else:
                status, due = "upcoming", next_due
        items.append(
            PaymentTimelineItem(
                id=bill.id, kind="bill", name=bill.name,
                amount_minor=bill.amount_minor, currency=currency,
                due_date=due, status=status, paid=paid,
            )
        )

    # --- Credit cards: the payment is the (pay-in-full) balance; the due day is
    # inferred from the card's own payment history.
    balances = {b.account_id: b for b in repository.list_account_balances(engine, household_id)}
    for account in repository.list_liability_accounts(engine, household_id):
        if account.currency != currency or account.account_type != "credit_card":
            continue
        balance = balances.get(account.id)
        owed = -balance.balance_minor if balance is not None and balance.balance_minor < 0 else 0
        payments = _account_payments(by_account.get(account.id, []))
        if owed <= 0 and not payments:
            continue  # inactive card: nothing owed, no history
        items.append(
            _liability_item(
                account.id, "credit_card", account.name, owed, currency,
                payments, today=today, horizon=horizon,
                stored_due_date=account.next_payment_due_date,
            )
        )

    # --- Loans & leases: expected amount is the recorded monthly payment; due day
    # inferred the same way. Payroll-deducted loans never claim checking cash.
    for obligation in recurring_liability_obligations(engine, household_id, currency):
        if obligation.kind == "retirement_loan":
            continue
        payments = _account_payments(by_account.get(obligation.account_id, []))
        items.append(
            _liability_item(
                obligation.account_id, obligation.kind, obligation.name,
                obligation.amount_minor, currency, payments,
                today=today, horizon=horizon,
                stored_due_date=obligation.next_payment_due_date,
            )
        )

    items.sort(
        key=lambda i: (_TIMELINE_STATUS_ORDER.get(i.status, 9), i.due_date or date.max)
    )

    due_total = sum(i.amount_minor for i in items if i.status in ("overdue", "due_soon"))
    liquid = sum(
        b.balance_minor
        for b in balances.values()
        if b.account_type in LIQUID_ACCOUNT_TYPES and b.currency == currency
    )
    return PaymentTimeline(
        items=items,
        due_total_minor=due_total,
        liquid_minor=liquid,
        covered=liquid >= due_total,
        window_days=window_days,
    )


def _liability_item(
    account_id: str,
    kind: str,
    name: str,
    amount_minor: int,
    currency: str,
    payments: list[TimelinePayment],
    *,
    today: date,
    horizon: date,
    stored_due_date: date | None = None,
) -> PaymentTimelineItem:
    """A card/loan/lease timeline entry.

    A stored due date (read off a statement or set by hand, ADR 0033) is
    authoritative — the day is known, not guessed. Otherwise the day is inferred
    from the account's own payment history; with neither, the item is undated and
    never flagged overdue (inference isn't strong enough to accuse a missed
    payment). A recent payment still marks the item paid in every case."""
    last = payments[0] if payments else None
    if stored_due_date is not None:
        next_due = stored_due_date
        while next_due < today:  # roll a stale stored date forward to this cycle
            next_due = add_months(next_due, 1)
    elif last is not None:
        next_due = add_months(last.occurred_at, 1)
        while next_due < today:  # inferred dates roll forward, never flag overdue
            next_due = add_months(next_due, 1)
    else:
        return PaymentTimelineItem(
            id=account_id, kind=kind, name=name, amount_minor=amount_minor,
            currency=currency, due_date=None, status="no_date", paid=None,
        )
    recently_paid = last is not None and (today - last.occurred_at).days <= 31
    if next_due <= horizon:
        status = "due_soon"
    elif recently_paid:
        status = "paid"
    else:
        status = "upcoming"
    return PaymentTimelineItem(
        id=account_id, kind=kind, name=name, amount_minor=amount_minor,
        currency=currency, due_date=next_due, status=status,
        paid=last if status == "paid" else None,
    )


@dataclass(frozen=True, slots=True)
class SubscriptionForecastItem:
    name: str
    amount_minor: int
    currency: str
    next_charge: date


def _subscriptions_category_id(engine: Engine, household_id: str) -> str | None:
    for category in repository.list_categories(engine, household_id):
        if category.name.strip().lower() == "subscriptions":
            return category.id
    return None


def subscription_forecast(
    engine: Engine,
    household_id: str,
    currency: str,
    *,
    today: date | None = None,
    horizon_days: int = SAFE_TO_SPEND_HORIZON_DAYS,
) -> tuple[list[SubscriptionForecastItem], Money]:
    """Recurring charges in the 'Subscriptions' category whose NEXT occurrence lands
    within the horizon and isn't yet paid this cycle (M109, ADR 0020). Reserved the
    'bill way': only the upcoming in-window charge, never a monthly total, so a
    charge already deducted from liquid is never double-counted."""
    from family_cfo_api import bill_detection

    today = today or date.today()
    category_id = _subscriptions_category_id(engine, household_id)
    if category_id is None:
        return [], Money.zero(currency)

    since = today - timedelta(days=bill_detection.LOOKBACK_DAYS)
    detection = [
        bill_detection.DetectionTransaction(
            occurred_at=txn.occurred_at,
            amount_minor=txn.amount_minor,
            currency=txn.currency,
            merchant=txn.merchant,
            description=txn.description,
        )
        for txn in repository.list_transactions(
            engine, household_id, limit=100_000, start=since, end=today
        )
        if txn.category_id == category_id and txn.currency == currency and txn.amount_minor < 0
    ]

    # A subscription already tracked as a Bill is reserved via bills_due — exclude
    # it here so the same charge is never reserved twice (ADR 0020 invariant).
    bill_keys = {
        bill_detection.normalize_merchant(bill.name)
        for bill in repository.list_bills(engine, household_id)
    }

    horizon_end = today + timedelta(days=horizon_days)
    items: list[SubscriptionForecastItem] = []
    total = Money.zero(currency)
    for candidate in bill_detection.detect_bill_candidates(detection):
        if candidate.merchant_key in bill_keys:
            continue
        # Only the next charge, and only if it's coming (not one already paid).
        if today <= candidate.next_due_date <= horizon_end:
            items.append(
                SubscriptionForecastItem(
                    name=candidate.name,
                    amount_minor=candidate.amount_minor,
                    currency=candidate.currency,
                    next_charge=candidate.next_due_date,
                )
            )
            total += Money(candidate.amount_minor, candidate.currency)
    items.sort(key=lambda item: item.next_charge)
    return items, total


def compute_safe_to_spend(
    engine: Engine,
    household_id: str,
    currency: str,
    *,
    horizon_days: int = SAFE_TO_SPEND_HORIZON_DAYS,
    today: date | None = None,
) -> tuple[CalculationResult, str]:
    """What the family can actually spend today, net of everything already owed.

    The advisor used to answer "how much can I spend?" by subtracting the
    emergency fund from liquid cash and calling the rest discretionary. That
    ignored every bill about to land and every minimum debt payment due, which
    overstated the answer by precisely the amount the family owed.
    """
    balances = repository.list_account_balances(engine, household_id)
    liquid_balance = Money.zero(currency)
    for balance in balances:
        if balance.account_type in LIQUID_ACCOUNT_TYPES and balance.currency == currency:
            liquid_balance += Money(balance.balance_minor, balance.currency)

    # Only money the family EXPLICITLY earmarked is held back. emergency_fund_inputs
    # falls back to treating all liquid cash as the fund when nothing is designated
    # (M36) — correct for measuring coverage, but here it would reserve every last
    # cent and report that nothing is ever spendable.
    fund = emergency_fund_inputs(engine, household_id, currency)
    reserved = fund.fund if fund.using_designations else Money.zero(currency)

    bills_due = Money.zero(currency)
    for bill in upcoming_bills(
        engine, household_id, currency, today=today, window_days=horizon_days
    ):
        if bill.amount.currency == currency:
            bills_due += bill.amount

    # M109 (ADR 0020): recurring subscriptions' next in-window charge — reserved the
    # 'bill way' so an already-paid charge is never double-counted.
    _, subscription_forecast_total = subscription_forecast(
        engine, household_id, currency, today=today, horizon_days=horizon_days
    )

    # Every liability the household carries, as a positive amount. Not subtracted
    # (a balance is not due this month) but reported: "$6,765 to spend" said
    # beside a silent $29,931 of card debt is true and still misleading.
    total_debt = Money.zero(currency)
    for balance in balances:
        if (
            balance.currency == currency
            and balance.balance_minor < 0
            # A 401(k) loan is owed to yourself, not an external creditor — keep it
            # out of the "total debt" figure (it's netted against retirement).
            and balance.account_type not in repository.RETIREMENT_LOAN_TYPES
        ):
            total_debt += Money(-balance.balance_minor, balance.currency)

    # A liability that is also set up as an explicit bill is already reserved via
    # bills_due above; mark it modeled so it isn't warned as unrecorded, but don't
    # subtract its minimum again — that would reserve the same payment twice (ADR 0032).
    bill_covered_accounts = bill_covered_account_ids(
        repository.list_bills(engine, household_id),
        repository.list_liability_accounts(engine, household_id),
    )
    minimum_debt_payments = Money.zero(currency)
    modeled_ids: set[str] = set(bill_covered_accounts)
    for debt in repository.list_debts_with_terms(engine, household_id):
        if debt.currency != currency or debt.minimum_payment_minor is None:
            continue
        # Modeled either way, so it never trips the "no minimum recorded" warning.
        modeled_ids.add(debt.account_id)
        if debt.account_id in bill_covered_accounts:
            continue
        # A 401(k) loan is repaid by payroll deduction — the money is withheld from
        # the paycheck before it ever reaches the bank, so it makes no claim on
        # liquid cash. Track it for payoff, but don't subtract it from safe-to-spend
        # (income already reflects the smaller paycheck; subtracting here double-counts).
        if debt.account_type in repository.RETIREMENT_LOAN_TYPES:
            continue
        minimum_debt_payments += Money(debt.minimum_payment_minor, debt.currency)

    # M106: the loop above only counts liabilities with a balance to pay down, so a
    # LEASE — a monthly payment with no payoff balance — was silently skipped even
    # though it claims cash every month. Reserve any remaining liability that has a
    # recorded payment, excluding cards (handled by the cards line) and 401(k) loans
    # (payroll-deducted). Already-counted debts are in `modeled_ids`, so no double-count.
    for account in repository.list_liability_accounts(engine, household_id):
        if (
            account.currency != currency
            or account.minimum_payment_minor is None
            or account.id in modeled_ids
            or account.account_type == "credit_card"
            or account.account_type in repository.RETIREMENT_LOAN_TYPES
        ):
            continue
        modeled_ids.add(account.id)
        minimum_debt_payments += Money(account.minimum_payment_minor, account.currency)

    # M96: a household that pays its cards in full each month has its whole card
    # balance about to leave liquid cash — commit the full balances, not just the
    # minimum, and treat those cards as modeled so they aren't warned as unrecorded.
    household = repository.get_household(engine, household_id)
    credit_card_payments = Money.zero(currency)
    if household is not None and household.credit_cards_paid_in_full:
        for balance in balances:
            if (
                balance.account_type == "credit_card"
                and balance.currency == currency
                and balance.balance_minor < 0
            ):
                credit_card_payments += Money(-balance.balance_minor, balance.currency)
                modeled_ids.add(balance.account_id)

    # A liability with no recorded minimum payment contributes nothing to the
    # subtraction, so its claim on the cash is invisible. Count AND total them,
    # so the warning can name the number rather than gesture at it.
    unmodeled = 0
    unmodeled_total = Money.zero(currency)
    for balance in balances:
        if balance.balance_minor >= 0 or balance.currency != currency:
            continue
        if balance.account_id in modeled_ids:
            continue
        unmodeled += 1
        unmodeled_total += Money(-balance.balance_minor, balance.currency)

    result = calculate_safe_to_spend(
        SafeToSpendInputs(
            liquid_balance=liquid_balance,
            emergency_fund_reserved=reserved,
            bills_due=bills_due,
            minimum_debt_payments=minimum_debt_payments,
            credit_card_payments=credit_card_payments,
            subscription_forecast=subscription_forecast_total,
            horizon_days=horizon_days,
            total_debt=total_debt,
            unmodeled_debt_count=unmodeled,
            unmodeled_debt_total=unmodeled_total,
        )
    )
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


def autocategorize_by_history(engine: Engine, household_id: str) -> int:
    """M96 rule (minimize duplicate input): file still-uncategorized transactions
    under the category the household has already assigned to that merchant — so a
    synced or imported repeat of a known merchant (Starbucks → Dining) doesn't
    have to be categorized again. The merchant's most-common past category wins,
    ties breaking to the most recent. Returns how many were auto-filed."""
    from collections import Counter

    from family_cfo_api.bill_detection import normalize_merchant

    # Newest-first, so a tie in the Counter resolves to the most recent choice.
    # Key on (merchant, is_inflow) so an inflow category (a Broadcom RSU deposit
    # filed as Income) never leaks onto an outflow of the same merchant, and vice
    # versa — that cross-sign leak is what put "Income" in the spending breakdown.
    txns = repository.list_transactions(engine, household_id, limit=100_000)
    by_merchant: dict[tuple[str, bool], Counter] = {}
    uncategorized: list[tuple[str, tuple[str, bool]]] = []
    for txn in txns:
        merchant_key = normalize_merchant(txn.merchant or txn.description)
        if not merchant_key:
            continue
        key = (merchant_key, txn.amount_minor >= 0)
        if txn.category_id is not None:
            by_merchant.setdefault(key, Counter())[txn.category_id] += 1
        else:
            uncategorized.append((txn.id, key))

    learned = {key: counter.most_common(1)[0][0] for key, counter in by_merchant.items()}

    to_set: dict[str, list[str]] = {}
    for txn_id, key in uncategorized:
        category_id = learned.get(key)
        if category_id is not None:
            to_set.setdefault(category_id, []).append(txn_id)

    return sum(
        repository.set_transactions_category(engine, household_id, ids, category_id)
        for category_id, ids in to_set.items()
    )


def propagate_category_to_merchant(
    engine: Engine, household_id: str, transaction_id: str, category_id: str
) -> int:
    """When one transaction is categorized, file every OTHER still-uncategorized
    transaction of the same merchant under the same category (minimize duplicate
    input). Runs server-side so it happens no matter which screen did the
    categorizing — the Categorize tab, the Overview drill-down, anywhere. Only
    fills blanks (never overwrites an existing category) and is sign-aware, so an
    inflow of a merchant never inherits an outflow's category. Returns the count."""
    from family_cfo_api.bill_detection import normalize_merchant

    target = repository.get_transaction(engine, household_id, transaction_id)
    if target is None:
        return 0
    key = normalize_merchant(target.merchant or target.description)
    if not key:
        return 0
    is_inflow = target.amount_minor >= 0
    ids = [
        t.id
        for t in repository.list_transactions(engine, household_id, limit=100_000)
        if t.id != transaction_id
        and t.category_id is None
        and (t.amount_minor >= 0) == is_inflow
        and normalize_merchant(t.merchant or t.description) == key
    ]
    return repository.set_transactions_category(engine, household_id, ids, category_id)


# Inflow labels that are earnings, not self-transfers — interest and dividends
# are income and must never be swept into Transfers.
_INCOME_TEXT_MARKERS = ("interest", "dividend")

# Outflow labels that move money out to the household's own accounts (or pay a
# card) rather than buy anything. Substring "transfer" catches online/internal/
# wire/requested transfers; "credit card" catches issuer-named payments like
# "American Express Credit Card". Kept narrow so a real expense ("mortgage
# payment") is never mistaken for a transfer.
_TRANSFER_OUTFLOW_MARKERS = ("transfer", "credit card", "card payment", "wire")

# Inflow labels that are a card payment landing on the card — never income, even
# when the paying account isn't linked so there's no outflow to match.
_CARD_PAYMENT_MARKERS = (
    "credit card payment",
    "card payment",
    "automatic payment",
    "payment thank you",
)


def _txn_text(txn: repository.TransactionRecord) -> str:
    return f"{txn.merchant or ''} {txn.description or ''}".lower()


def _looks_like_income(txn: repository.TransactionRecord) -> bool:
    return txn.amount_minor > 0 and any(m in _txn_text(txn) for m in _INCOME_TEXT_MARKERS)


def _outflow_dates_by_amount(
    txns: list[repository.TransactionRecord],
) -> dict[int, list[date]]:
    """Index of outflow magnitudes → the dates they left a linked account, so an
    inflow can be matched to money that demonstrably left the household."""
    index: dict[int, list[date]] = {}
    for txn in txns:
        if txn.amount_minor < 0:
            index.setdefault(-txn.amount_minor, []).append(txn.occurred_at)
    return index


def _as_income_txn(txn: repository.TransactionRecord):
    from family_cfo_api import income_detection

    return income_detection.IncomeTransaction(
        id=txn.id,
        occurred_at=txn.occurred_at,
        amount_minor=txn.amount_minor,
        currency=txn.currency,
        merchant=txn.merchant,
        description=txn.description,
    )


def _is_transfer(
    txn: repository.TransactionRecord, outflows_by_amount: dict[int, list[date]]
) -> bool:
    """Money moving between the household's own accounts, not consumption or income.

    Earnings win first (an "Interest Payment" is income). An outflow is judged by
    label alone — it can never be hidden income. An inflow is a transfer only if it
    is a card payment, or the money can be matched to an outflow that left one of
    the household's own linked accounts — so a "Transfer from Schwab" or an
    "Online Transfer" paycheck with no matching outflow is treated as income
    arriving, not silently buried as a transfer.
    """
    from family_cfo_api import income_detection

    if _looks_like_income(txn):
        return False
    text = _txn_text(txn)
    if txn.amount_minor < 0:
        return any(m in text for m in _TRANSFER_OUTFLOW_MARKERS)
    if any(m in text for m in _CARD_PAYMENT_MARKERS):
        return True
    return income_detection.is_internal_transfer(_as_income_txn(txn), outflows_by_amount)


def _category_id_by_name(engine: Engine, household_id: str, names: tuple[str, ...]) -> str | None:
    return next(
        (
            cat.id
            for cat in repository.list_categories(engine, household_id)
            if cat.name.strip().lower() in names
        ),
        None,
    )


def autofile_income(engine: Engine, household_id: str) -> int:
    """M96 rule: recognise income the system can identify without asking — interest
    and dividend inflows, and recurring deposits (paychecks) detected from cadence
    even when the bank labels them "Online Transfer" — filing them under Income.
    Recurring deposits that match an outflow from a linked account are skipped
    (they are internal transfers, not pay). No-op without an Income category."""
    from family_cfo_api import income_detection

    income_category_id = _category_id_by_name(engine, household_id, ("income",))
    if income_category_id is None:
        return 0

    txns = repository.list_transactions(engine, household_id, limit=100_000)
    outflows_by_amount = _outflow_dates_by_amount(txns)
    by_id = {t.id: t for t in txns}
    to_file: set[str] = {
        t.id for t in txns if t.category_id is None and _looks_like_income(t)
    }

    inflows = [_as_income_txn(t) for t in txns if t.amount_minor > 0]
    for candidate in income_detection.detect_income_sources(inflows):
        for member in candidate.transactions:
            record = by_id.get(member.id)
            if (
                record is not None
                and record.category_id is None
                and not income_detection.is_internal_transfer(member, outflows_by_amount)
            ):
                to_file.add(member.id)

    if not to_file:
        return 0
    return repository.set_transactions_category(
        engine, household_id, list(to_file), income_category_id
    )


# Outflow labels that are tax withholding, not discretionary spending. "gencash"
# is Charles Schwab's ledger term for the sell-to-cover cash that goes to tax
# withholding when an equity award (RSU) vests ("Gencash … Lapse").
_TAX_TEXT_MARKERS = ("gencash", "tax withholding")


def _looks_like_tax(txn: repository.TransactionRecord) -> bool:
    return txn.amount_minor < 0 and any(m in _txn_text(txn) for m in _TAX_TEXT_MARKERS)


def autofile_taxes(engine: Engine, household_id: str) -> int:
    """M96 rule: file tax-withholding outflows (RSU sell-to-cover, "Gencash …
    Lapse") under a Taxes category so they are tracked on their own and kept out
    of discretionary spending, instead of looking like a large mystery purchase.
    Creates the Taxes category when a tax outflow exists and none is present yet.
    Returns how many were filed."""
    txns = repository.list_transactions(engine, household_id, limit=100_000)
    ids = [t.id for t in txns if t.category_id is None and _looks_like_tax(t)]
    if not ids:
        return 0
    tax_category_id = _category_id_by_name(engine, household_id, repository.TAXES_CATEGORY_NAMES)
    if tax_category_id is None:
        tax_category_id = repository.create_category(engine, household_id, "Taxes").id
    return repository.set_transactions_category(engine, household_id, ids, tax_category_id)


def monthly_taxes_total(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> Money:
    """Tax withheld, averaged over the trailing 12 complete months to a monthly
    figure (like income) — lumpy RSU withholdings spread into a fair run-rate."""
    today = today or date.today()
    this_month_start = today.replace(day=1)
    window_start = add_months(this_month_start, -INCOME_TRAILING_MONTHS)
    window_end = this_month_start - timedelta(days=1)
    total = repository.sum_taxes(engine, household_id, window_start, window_end, currency)
    return Money(total // INCOME_TRAILING_MONTHS, currency)


def autofile_all(engine: Engine, household_id: str) -> tuple[int, int]:
    """M96 rule: keep freshly-imported transactions out of the Categorize queue when
    the system can already tell where they go — interest/dividends recognised as
    income, RSU sell-to-cover as taxes, transfers filed under Transfers, and a known
    merchant reusing the category the user gave it before. Income runs first so an
    "Interest Payment" is recognised as earnings, not swept into transfers. Runs on
    EVERY sync path (manual, initial, and the daily worker). Returns
    (transfers_filed, auto_categorized)."""
    autofile_income(engine, household_id)
    autofile_taxes(engine, household_id)
    transfers_filed = autofile_transfers(engine, household_id)
    auto_categorized = autocategorize_by_history(engine, household_id)
    # M97: surface exact-duplicate charges (same account/date/amount/merchant) for
    # the Review queue so the user can dispute a double-charge.
    repository.flag_possible_duplicates(engine, household_id)
    return transfers_filed, auto_categorized


def autofile_transfers(engine: Engine, household_id: str) -> int:
    """M96 rule (minimize duplicate input): file still-uncategorized transactions
    that are money moving between the household's own accounts under the Transfers
    category, so they leave the Categorize queue and their outflow side stops
    inflating spending. No-op if the household has no Transfers category. Returns
    how many were filed."""
    transfer_category_id = _category_id_by_name(
        engine, household_id, repository.TRANSFER_CATEGORY_NAMES
    )
    if transfer_category_id is None:
        return 0

    txns = repository.list_transactions(engine, household_id, limit=100_000)
    outflows_by_amount = _outflow_dates_by_amount(txns)
    ids = [
        t.id for t in txns if t.category_id is None and _is_transfer(t, outflows_by_amount)
    ]
    if not ids:
        return 0
    return repository.set_transactions_category(engine, household_id, ids, transfer_category_id)


def goal_current_minor(
    engine: Engine, household_id: str, goal: repository.GoalRecord
) -> int:
    """A goal's real progress. An emergency-fund goal tracks the household's
    DESIGNATED emergency fund (the same figure the Overview's Emergency Fund card
    shows) — otherwise the goal reads $0 while the fund holds real money (M41
    fix). Only when the family has actually earmarked emergency money: with no
    designation there's no live figure to trust, so it falls back to the stored
    current. Every other goal type uses its stored current."""
    if goal.goal_type == "emergency_fund":
        ef = emergency_fund_inputs(engine, household_id, goal.currency)
        if ef.using_designations:
            return ef.fund.amount_minor
    return goal.current_minor


INCOME_TRAILING_MONTHS = 12


def monthly_income_total(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> Money:
    """This year's income = actual money landing in the household's accounts.

    Two sources, both "money in": any confirmed recurring income sources, plus the
    trailing-12-complete-month average of inflows filed under the Income category
    (paychecks, interest, dividends, RSU proceeds) — averaged so a lumpy RSU vest
    or quarterly dividend is spread into a fair monthly run-rate.

    The W2 / compensation profile is deliberately NOT counted here: it is last
    year's document, kept as a baseline and to drive tax prediction, not as this
    year's cash flow.
    """
    total = Money.zero(currency)
    for income in repository.list_income_sources(engine, household_id):
        recurring = RecurringAmount(
            income.name, Money(income.amount_minor, income.currency), income.frequency
        )
        total += recurring.monthly_amount()

    today = today or date.today()
    this_month_start = today.replace(day=1)
    window_start = add_months(this_month_start, -INCOME_TRAILING_MONTHS)
    window_end = this_month_start - timedelta(days=1)
    trailing = repository.sum_income(engine, household_id, window_start, window_end, currency)
    total += Money(trailing // INCOME_TRAILING_MONTHS, currency)
    return total


def w2_baseline_monthly(engine: Engine, household_id: str, currency: str) -> Money | None:
    """Monthly gross implied by the W2 / compensation profiles, or None if the
    household declared none. This is a baseline reference shown next to actual
    income (which is net money-in) — deliberately NOT part of monthly_income_total.
    Gross = base + RSU + bonus, matching how the profile is declared."""
    profiles = repository.list_income_profiles(engine, household_id)
    if not profiles:
        return None
    annual_gross = sum(
        profile.base_salary_minor
        + profile.rsu_annual_minor
        + int(profile.base_salary_minor * profile.bonus_percent / 100)
        for profile in profiles
    )
    return Money(annual_gross // 12, currency)


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


def _monthly_debt_minimums(engine: Engine, household_id: str, currency: str) -> Money:
    """Monthly minimum payments on loans, cards, and other liabilities that make a
    recurring claim on liquid cash — for the emergency-fund denominator (ADR 0039).

    Deduped against bills: a debt also modeled as an explicit bill is already in
    ``_monthly_bill_total``, so it is skipped here (``bill_covered_account_ids``).
    401(k) loans are excluded — they are repaid by payroll deduction and never touch
    the bank, so income already reflects the smaller paycheck (mirrors the
    safe-to-spend treatment). Cards contribute their MINIMUM, not the full balance:
    in an emergency you pay the minimum to stay current, not the statement balance."""
    liability_accounts = repository.list_liability_accounts(engine, household_id)
    bill_covered = bill_covered_account_ids(
        repository.list_bills(engine, household_id), liability_accounts
    )
    total = Money.zero(currency)
    modeled_ids: set[str] = set(bill_covered)
    for debt in repository.list_debts_with_terms(engine, household_id):
        if debt.currency != currency or debt.minimum_payment_minor is None:
            continue
        modeled_ids.add(debt.account_id)
        if debt.account_id in bill_covered or debt.account_type in repository.RETIREMENT_LOAN_TYPES:
            continue
        total += Money(debt.minimum_payment_minor, debt.currency)
    # Liabilities without a payoff balance (a lease, a card carried at its minimum)
    # never appear in list_debts_with_terms but still claim cash every month.
    for account in liability_accounts:
        if (
            account.currency != currency
            or account.minimum_payment_minor is None
            or account.id in modeled_ids
            or account.account_type in repository.RETIREMENT_LOAN_TYPES
        ):
            continue
        modeled_ids.add(account.id)
        total += Money(account.minimum_payment_minor, account.currency)
    return total


def monthly_essential_expenses(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> Money:
    """The realistic monthly cash a household must cover if income stopped — the
    emergency-fund coverage denominator (ADR 0039).

    ``= recurring bills + debt minimum payments + everyday spending above bills``

    Bill payments are categorized transactions, so they are ALREADY inside average
    spending; debt minimum payments are transfers, so they are NOT. To count every
    dollar once we take trailing-3-month average spending, strip the bill portion
    back out (``max(0, avg - bills)``), then add the explicit bills and the debt
    minimums that no bill already covers. Bills-only — the previous denominator —
    was absurdly optimistic: it ignored groceries, gas, and every loan/card payment,
    so a fund covered "months" of a household that in reality spends far more."""
    today = today or date.today()
    this_month_start = today.replace(day=1)
    # Last 3 complete calendar months, matching the savings-rate window (M44).
    window_start = add_months(this_month_start, -3)
    window_end = this_month_start - timedelta(days=1)

    bills = _monthly_bill_total(engine, household_id, currency)
    debt_minimums = _monthly_debt_minimums(engine, household_id, currency)

    spending_3mo = repository.sum_spending(engine, household_id, window_start, window_end, currency)
    avg_spending_minor = max(0, round(spending_3mo / 3))
    # Average spending already contains the bill-categorized payments; keep only the
    # part above the recurring bills so housing/utilities aren't counted twice.
    spending_above_bills = Money(max(0, avg_spending_minor - bills.amount_minor), currency)

    return bills + debt_minimums + spending_above_bills


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
