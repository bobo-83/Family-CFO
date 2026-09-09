from __future__ import annotations

import calendar
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
    RetirementAgeSolveInput,
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
    solve_retirement_age,
)
from sqlalchemy.engine import Engine

from family_cfo_api import audit, household_clock, repository, undo_actions
from family_cfo_api.household_crypto import SealedAmountUnreadableError
from family_cfo_api.qualified_amounts import (
    Qualified,
    QualifiedRows,
    SourceSet,
    UnreadableAmountSource,
    union_sources,
)

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
_MONTH_STEP_BY_FREQUENCY = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}


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


# --- shared account read (M122) ---------------------------------------------
#
# ONE assembler behind every account inventory the household can see: the
# Accounts tab (`GET /accounts`) and the advisor's `get_accounts` tool project
# the same view, so the two can never drift into showing different accounts,
# institutions, or emergency-fund reservations.


@dataclass(frozen=True, slots=True)
class AccountView:
    """One account as both the Accounts tab and the advisor see it.

    `balance_minor` is the stored balance, SIGNED and unmodified: a liability
    owing money is negative, exactly as the account list shows it — but a paid-
    off card sits at zero and an overpaid or refunded one goes positive, so the
    sign is a reading of the balance, not a promise about the account type.
    `get_debt_outlook` stays the authority on the positive amount owed, rates,
    minimums, and payoff.
    """

    account_id: str
    name: str
    account_type: str
    currency: str
    balance_minor: int
    # Spendability category (M33) for assets; None for liabilities, which have
    # no entry in ASSET_CATEGORY_BY_TYPE by design.
    spendability_category: str | None
    is_liability: bool
    annual_interest_rate: float | None
    minimum_payment_minor: int | None
    maturity_date: date | None
    next_payment_due_date: date | None
    # M36: the designation (percent XOR fixed) and the reservation derived from
    # it. `emergency_fund_reserved_minor` is None when nothing is designated —
    # "no reservation" and "a reservation of zero" are different facts.
    emergency_fund_percent: float | None
    emergency_fund_minor: int | None
    emergency_fund_reserved_minor: int | None
    rsu_ready_to_sell: bool
    institution: str | None
    last_synced_at: datetime | None


def list_account_views(engine: Engine, household_id: str) -> list[AccountView]:
    """Every account with a balance, assembled once for the app and the advisor.

    Inherited blind spot, deliberate (M122): `list_account_balances` joins the
    latest balance snapshot, so an account that has never had a balance recorded
    is absent here — as it is from `GET /accounts`. The advisor therefore sees
    exactly the inventory the household sees on the Accounts tab, rather than a
    second, differently-incomplete list.
    """
    balances = repository.list_account_balances(engine, household_id)
    connections = repository.account_connection_map(engine, household_id)
    # Prefer the real per-account institution (SimpleFIN's org, e.g. "Charles
    # Schwab") over the generic connection name ("SimpleFin (multiple banks)").
    institutions = repository.account_institution_map(engine, household_id)
    views: list[AccountView] = []
    for balance in balances:
        connection = connections.get(balance.account_id)
        designated = (
            balance.emergency_fund_percent is not None or balance.emergency_fund_minor is not None
        )
        views.append(
            AccountView(
                account_id=balance.account_id,
                name=balance.name,
                account_type=balance.account_type,
                currency=balance.currency,
                balance_minor=balance.balance_minor,
                spendability_category=ASSET_CATEGORY_BY_TYPE.get(balance.account_type),
                is_liability=balance.account_type in repository.LIABILITY_ACCOUNT_TYPES,
                annual_interest_rate=balance.annual_interest_rate,
                minimum_payment_minor=balance.minimum_payment_minor,
                maturity_date=balance.maturity_date,
                next_payment_due_date=balance.next_payment_due_date,
                emergency_fund_percent=balance.emergency_fund_percent,
                emergency_fund_minor=balance.emergency_fund_minor,
                emergency_fund_reserved_minor=(
                    repository.emergency_fund_reserved_minor(
                        balance.emergency_fund_percent,
                        balance.emergency_fund_minor,
                        balance.balance_minor,
                    )
                    if designated
                    else None
                ),
                rsu_ready_to_sell=balance.rsu_ready_to_sell,
                institution=(
                    institutions.get(balance.account_id)
                    or (connection.institution if connection else None)
                ),
                last_synced_at=connection.last_synced_at if connection else None,
            )
        )
    return views


# --- base-currency partition (M123, ADR 0075, #152) ---------------------------
#
# The app is single-currency per household: every figure it computes is in the
# household's base currency, and a balance held in another currency is EXCLUDED
# from that figure and DISCLOSED — never converted (ADR 0003 keeps the engine
# deterministic and offline; ADR 0014 keeps exchange rates a conversational
# tool). Every path that adds balances together partitions them here, so the
# aggregators that used to feed mixed currencies to `Money.__add__` — and take
# the Overview down on the first foreign account — cannot drift apart again.
# `Money.__add__` itself still raises on a mismatch: that is the engine's
# invariant, and the bug was callers handing it mixed input.


@dataclass(frozen=True, slots=True)
class ExcludedAccount:
    """An account a base-currency figure left out because it is held in another
    currency. ``balance`` is typed Money in the account's OWN currency, so the
    amount travels with its unit and can never be read as base-currency money."""

    account_id: str
    name: str
    account_type: str
    balance: Money


@dataclass(frozen=True, slots=True)
class CurrencyPartition:
    """``list_account_balances`` split into what a base-currency figure may add
    up (``in_base``) and what it must leave out and disclose (``foreign``).

    ``foreign`` is eligibility-specific when the caller passes ``eligible``: it
    holds only the out-of-base accounts that WOULD have been components of the
    figure (net worth skips 401(k) loans regardless; safe-to-spend counts only
    liquid types), so a disclosure never claims an account was left out of a
    total it was never part of. Without a predicate it is the global fact —
    every account outside the base currency — which is what the Overview lists.
    """

    currency: str
    in_base: list[repository.AccountBalanceRecord]
    foreign: list[repository.AccountBalanceRecord]

    @property
    def excluded_accounts(self) -> list[ExcludedAccount]:
        return [
            ExcludedAccount(
                b.account_id, b.name, b.account_type, Money(b.balance_minor, b.currency)
            )
            for b in self.foreign
        ]


def partition_balances_by_currency(
    balances: Iterable[repository.AccountBalanceRecord],
    currency: str,
    *,
    eligible: Callable[[repository.AccountBalanceRecord], bool] | None = None,
) -> CurrencyPartition:
    # Defensive (#152 review): the engine's Money canonicalises codes to upper
    # case and ingress now does too, but a legacy "usd" row must never be read
    # as foreign to its own USD household.
    currency = currency.upper()
    in_base: list[repository.AccountBalanceRecord] = []
    foreign: list[repository.AccountBalanceRecord] = []
    for balance in balances:
        if balance.currency.upper() == currency:
            in_base.append(balance)
        elif eligible is None or eligible(balance):
            foreign.append(balance)
    return CurrencyPartition(currency, in_base, foreign)


def _merge_excluded(*groups: Iterable[ExcludedAccount]) -> list[ExcludedAccount]:
    """One disclosure per account, first mention wins, order preserved."""
    merged: dict[str, ExcludedAccount] = {}
    for group in groups:
        for account in group:
            merged.setdefault(account.account_id, account)
    return list(merged.values())


def _currencies_of(excluded: Sequence[ExcludedAccount]) -> str:
    return " and ".join(sorted({a.balance.currency for a in excluded}))


def foreign_currency_warning(excluded: Sequence[ExcludedAccount], currency: str) -> str | None:
    """One sentence saying that — and how many — accounts a figure left out.

    Deliberately GENERIC: it counts accounts and names currencies, never the
    accounts. Warnings are persisted in plaintext (`financial_calculations` and
    `recommendations` ``warnings_json``) while account names are sealed content
    (ADR 0072), and the grounding guardrail treats a warning as app-authored
    text whose digits may ground a figure, while it strips the digits out of a
    ``name``. Names and balances travel in the typed, live ``excluded_accounts``
    field instead, where a name stays non-grounding and a balance keeps its own
    currency.
    """
    if not excluded:
        return None
    count = len(excluded)
    noun, verb = ("account", "is") if count == 1 else ("accounts", "are")
    return (
        f"{count} {noun} held in {_currencies_of(excluded)} {verb} not counted in this "
        f"{currency} figure; a balance in another currency is never converted."
    )


def ignored_designation_warning(excluded: Sequence[ExcludedAccount], currency: str) -> str | None:
    """M36 designations on an out-of-base account cannot join a base-currency
    fund, so they are ignored — and, unlike before, said to be ignored."""
    if not excluded:
        return None
    count = len(excluded)
    noun = "account" if count == 1 else "accounts"
    return (
        f"An emergency-fund designation on {count} {noun} held in {_currencies_of(excluded)} "
        f"is ignored; designations count only in {currency}."
    )


QUALIFIED_ATTEMPT_VERSION = "qualified-attempt/1"
INCOMPLETE_WARNING = (
    "Some stored amounts could not be read; dependent decisions are unavailable until repaired."
)


@dataclass(slots=True)
class CalculationAttempt:
    """Application-owned envelope for complete and gated calculations."""

    calculation_type: str
    inputs: dict[str, Any]
    assumptions: list[str]
    warnings: list[str]
    outputs: dict[str, Any]
    incomplete_sources: SourceSet = frozenset()
    engine_result: CalculationResult | None = None
    response: Any = None

    @classmethod
    def complete(cls, result: CalculationResult) -> CalculationAttempt:
        return cls(
            calculation_type=result.calculation_type,
            inputs=result.inputs,
            assumptions=result.assumptions,
            warnings=result.warnings,
            outputs=result.outputs,
            engine_result=result,
        )

    @property
    def engine_invoked(self) -> bool:
        return self.engine_result is not None

    @property
    def version(self) -> str:
        return self.engine_result.version if self.engine_result is not None else QUALIFIED_ATTEMPT_VERSION


def _persist_attempt(
    engine: Engine,
    household_id: str,
    attempt: CalculationAttempt,
) -> str:
    inputs = dict(attempt.inputs)
    if not attempt.engine_invoked:
        inputs.update(
            {
                "attempt_schema": QUALIFIED_ATTEMPT_VERSION,
                "engine_invoked": False,
                "incomplete_amount_count": len(attempt.incomplete_sources),
            }
        )
    return repository.record_calculation(
        engine,
        household_id=household_id,
        calculation_type=attempt.calculation_type,
        version=attempt.version,
        inputs=inputs,
        assumptions=attempt.assumptions,
        warnings=attempt.warnings,
        outputs=_serialize_outputs(attempt.outputs),
    )


def _persist_attempt_disclosing(
    engine: Engine,
    household_id: str,
    attempt: CalculationAttempt,
    currency: str,
    excluded: Sequence[ExcludedAccount],
    *,
    warnings: Iterable[str | None] = (),
) -> str:
    for warning in (foreign_currency_warning(excluded, currency), *warnings):
        if warning and warning not in attempt.warnings:
            attempt.warnings.append(warning)
    attempt.inputs["excluded_account_count"] = len(excluded)
    attempt.inputs["excluded_currencies"] = sorted(
        {account.balance.currency for account in excluded}
    )
    calculation_id = _persist_attempt(engine, household_id, attempt)
    attempt.outputs["excluded_accounts"] = [
        {"name": account.name, "type": account.account_type, "balance": account.balance}
        for account in excluded
    ]
    return calculation_id


def _persist_disclosing(
    engine: Engine,
    household_id: str,
    result: CalculationResult,
    currency: str,
    excluded: Sequence[ExcludedAccount],
    *,
    warnings: Iterable[str | None] = (),
) -> str:
    """Persist a calculation together with what it left out (ADR 0003: the row
    must say so), then attach the typed disclosure to the LIVE result only.

    The persisted row carries the generic warning and the count/currencies —
    never an account name, because ``inputs_json`` / ``outputs_json`` /
    ``warnings_json`` are plaintext columns while names are sealed content.
    ``outputs["excluded_accounts"]`` is added after the write for exactly that
    reason: it reaches the tool payload and the API, not the audit row.
    """
    for warning in (foreign_currency_warning(excluded, currency), *warnings):
        if warning and warning not in result.warnings:
            result.warnings.append(warning)
    result.inputs["excluded_account_count"] = len(excluded)
    result.inputs["excluded_currencies"] = sorted({a.balance.currency for a in excluded})
    calculation_id = _persist(engine, household_id, result)
    result.outputs["excluded_accounts"] = [
        {"name": a.name, "type": a.account_type, "balance": a.balance} for a in excluded
    ]
    return calculation_id


def _designated(balance: repository.AccountBalanceRecord) -> bool:
    return balance.emergency_fund_percent is not None or balance.emergency_fund_minor is not None


def _counts_toward_net_worth(balance: repository.AccountBalanceRecord) -> bool:
    # Mirrors the engine's own rule: a 401(k) loan is net-worth-neutral and
    # skipped whatever its currency, so it is never "excluded" from the total.
    return balance.account_type not in repository.RETIREMENT_LOAN_TYPES


def _touches_safe_to_spend(balance: repository.AccountBalanceRecord) -> bool:
    # Liquid cash, a designated reservation, or a debt (reported beside the
    # figure and, with terms, subtracted as a minimum payment). A 401(k) loan
    # is owed to yourself and repaid by payroll: neither the debt total nor the
    # reserved payments ever count it, so it is never "excluded" from them.
    if balance.account_type in repository.RETIREMENT_LOAN_TYPES:
        return False
    return (
        balance.account_type in LIQUID_ACCOUNT_TYPES
        or _designated(balance)
        or balance.balance_minor < 0
    )


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
    today = today or household_clock.today_for_household(engine, household_id)
    horizon = today + timedelta(days=window_days or UPCOMING_BILL_WINDOW_DAYS)
    # An occurrence the user explicitly linked to its payment is settled — it
    # is no longer due and no longer a claim on cash.
    linked = {
        (link.bill_id, link.due_date)
        for link in repository.list_bill_payment_links(engine, household_id)
    }
    result: list[UpcomingBill] = []
    for bill in repository.list_bills(engine, household_id):
        if bill.next_due_date is None:
            continue
        due = next_bill_occurrence(bill.next_due_date, bill.frequency, today)
        if (bill.id, due) in linked:
            continue
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
    # #152: the out-of-base accounts this fund would otherwise have counted, and
    # the warnings that disclose them (generic — see foreign_currency_warning).
    excluded_accounts: list[ExcludedAccount] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def emergency_fund_inputs(engine: Engine, household_id: str, currency: str) -> EmergencyFundInputs:
    partition = partition_balances_by_currency(
        repository.list_account_balances(engine, household_id),
        currency,
        eligible=lambda b: b.account_type in LIQUID_ACCOUNT_TYPES or _designated(b),
    )
    liquid_balance = Money.zero(currency)
    designated_minor = 0
    for balance in partition.in_base:
        if balance.account_type in LIQUID_ACCOUNT_TYPES:
            liquid_balance += Money(balance.balance_minor, balance.currency)
        # M36: user-designated reservations, on any account type.
        designated_minor += repository.emergency_fund_reserved_minor(
            balance.emergency_fund_percent, balance.emergency_fund_minor, balance.balance_minor
        )
    using_designations = designated_minor > 0
    # M36: once the family designates emergency-fund money, coverage measures
    # that fund — not the legacy "all liquid money" approximation.
    fund = Money(designated_minor, currency) if using_designations else liquid_balance

    # #152: disclose only what this fund would have counted. A designation on a
    # foreign account is always ignored (and said to be); an undesignated
    # foreign liquid account was part of the fund only in the all-liquid
    # fallback, so once designations define the fund it is not "excluded".
    foreign_designated = [
        a
        for a, b in zip(partition.excluded_accounts, partition.foreign, strict=True)
        if _designated(b)
    ]
    excluded = foreign_designated if using_designations else partition.excluded_accounts
    warnings = [w for w in (ignored_designation_warning(foreign_designated, currency),) if w]
    return EmergencyFundInputs(fund, using_designations, excluded, warnings)


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
    engine: Engine,
    household_id: str,
    inputs: RetirementInput,
    *,
    excluded: Sequence[ExcludedAccount] = (),
) -> tuple[CalculationResult, str]:
    """``excluded`` (#152 review): the retirement/HSA accounts the grounding could
    not add up, so the audit row carries the same provenance as every other
    base-currency figure instead of a reference that knows nothing of them."""
    result = calculate_retirement_projection(inputs)
    calculation_id = _persist_disclosing(
        engine, household_id, result, inputs.current_savings.currency, excluded
    )
    return result, calculation_id


def compute_retirement_age_solve(
    engine: Engine,
    household_id: str,
    inputs: RetirementAgeSolveInput,
    *,
    excluded: Sequence[ExcludedAccount] = (),
) -> tuple[CalculationResult, str]:
    result = solve_retirement_age(inputs)
    calculation_id = _persist_disclosing(
        engine, household_id, result, inputs.current_savings.currency, excluded
    )
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
) -> Qualified[int]:
    """Net worth at the end of a past month, reconstructed from today's balances
    minus every transaction that has posted since. Approximate (it can't rewind
    market moves on investments), but far better than nothing for a month before
    daily net-worth snapshots existed. Returns the net-worth amount in minor units."""
    balances = repository.list_account_balances(engine, household_id)
    later = repository.iter_transaction_amount_candidates(
        engine, household_id, start=as_of + timedelta(days=1)
    )
    base_account_ids = {b.account_id for b in balances if b.currency == currency}
    posted_since: dict[str, int] = {}
    sources: set[UnreadableAmountSource] = set()
    for candidate in later:
        account_id = candidate.metadata.account_id
        if account_id not in base_account_ids:
            continue
        if candidate.amount is None:
            assert candidate.incomplete_source is not None
            sources.add(candidate.incomplete_source)
        else:
            posted_since[account_id] = posted_since.get(account_id, 0) + candidate.amount

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
    return Qualified(
        int(result.outputs["net_worth"].amount_minor), frozenset(sources)
    )


def reconstruct_debt_total(
    engine: Engine, household_id: str, as_of: date, currency: str
) -> Qualified[int]:
    """Total owed across all liability accounts at the end of a past date,
    reconstructed from today's balances minus the liability transactions posted
    since (mirrors reconstruct_net_worth). Approximate — a mortgage whose balance
    is synced rather than transacted won't rewind perfectly — but it is the only
    debt history that exists before the daily balance record began. Returns a
    positive amount owed in minor units."""
    balances = repository.list_account_balances(engine, household_id)
    liability_ids = {
        balance.account_id
        for balance in balances
        if balance.account_type in repository.LIABILITY_ACCOUNT_TYPES
        and balance.currency.upper() == currency.upper()
    }
    later = repository.iter_transaction_amount_candidates(
        engine, household_id, start=as_of + timedelta(days=1)
    )
    posted_since: dict[str, int] = {}
    sources: set[UnreadableAmountSource] = set()
    for candidate in later:
        account_id = candidate.metadata.account_id
        if account_id not in liability_ids:
            continue
        if candidate.amount is None:
            assert candidate.incomplete_source is not None
            sources.add(candidate.incomplete_source)
        else:
            posted_since[account_id] = posted_since.get(account_id, 0) + candidate.amount

    total_owed = 0
    for b in balances:
        if b.account_type in repository.LIABILITY_ACCOUNT_TYPES and b.currency == currency:
            reconstructed = b.balance_minor - posted_since.get(b.account_id, 0)
            total_owed += max(0, -reconstructed)  # a liability is a negative balance
    return Qualified(total_owed, frozenset(sources))


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
    today = today or household_clock.today_for_household(engine, household_id)
    earliest = repository.earliest_transaction_month(engine, household_id)
    points: list[DebtHistoryPoint] = []
    if earliest is not None:
        cursor = date(int(earliest[:4]), int(earliest[5:7]), 1)
        current_month_start = today.replace(day=1)
        while cursor <= current_month_start:
            # Month-end, except the current (partial) month uses today.
            as_of = today if cursor == current_month_start else add_months(cursor, 1) - timedelta(days=1)
            owed = reconstruct_debt_total(engine, household_id, as_of, currency)
            # Debt-history is an unstable historical product and has no partial
            # wire shape in Item 2. Keep its documented strict boundary until a
            # later contract explicitly qualifies it.
            if not owed.is_complete:
                raise SealedAmountUnreadableError(household_id)
            points.append(
                DebtHistoryPoint(
                    f"{cursor.year}-{cursor.month:02d}", Money(owed.value, currency)
                )
            )
            cursor = add_months(cursor, 1)
    average_minor = (
        round(sum(p.total_owed.amount_minor for p in points) / len(points)) if points else 0
    )
    return DebtHistory(points, Money(average_minor, currency), len(points))


def compute_net_worth_with_ref(
    engine: Engine, household_id: str, currency: str
) -> tuple[CalculationResult, str]:
    # #152: only base-currency balances reach the engine; the rest are disclosed.
    partition = partition_balances_by_currency(
        repository.list_account_balances(engine, household_id),
        currency,
        eligible=_counts_toward_net_worth,
    )
    engine_balances = [
        AccountBalance(b.account_id, b.account_type, Money(b.balance_minor, b.currency))
        for b in partition.in_base
    ]

    result = calculate_net_worth(engine_balances, currency)
    calculation_id = _persist_disclosing(
        engine, household_id, result, currency, partition.excluded_accounts
    )
    return result, calculation_id


def compute_emergency_fund(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> CalculationAttempt:
    result, _calculation_id = compute_emergency_fund_with_ref(
        engine, household_id, currency, today=today
    )
    return result


def compute_emergency_fund_with_ref(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> tuple[CalculationAttempt, str]:
    inputs = emergency_fund_inputs(engine, household_id, currency)
    # #152 review: the denominator has account-derived inputs of its own (the
    # liability minimums), so a foreign loan left out of it is disclosed too.
    expenses, denominator_excluded = monthly_essential_expenses_with_exclusions(
        engine, household_id, currency, today=today
    )
    if expenses.is_complete:
        attempt = CalculationAttempt.complete(
            calculate_emergency_fund_months(inputs.fund, expenses.value)
        )
    else:
        attempt = CalculationAttempt(
            calculation_type="emergency_fund",
            inputs={"incomplete_amount_count": expenses.incomplete_count},
            assumptions=[],
            warnings=[INCOMPLETE_WARNING],
            outputs={
                "liquid_balance": inputs.fund,
                "monthly_essential_expenses": expenses,
                "emergency_fund_months": None,
            },
            incomplete_sources=expenses.incomplete_sources,
        )
    calculation_id = _persist_attempt_disclosing(
        engine,
        household_id,
        attempt,
        currency,
        _merge_excluded(inputs.excluded_accounts, denominator_excluded),
        warnings=inputs.warnings,
    )
    return attempt, calculation_id


logger = logging.getLogger(__name__)

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


@dataclass(frozen=True, slots=True)
class RecurringIncomeCandidates:
    transactions: list[Any]
    candidates: list[Any]
    included_ids: set[str]
    excluded_ids: set[str]
    incomplete_by_currency: tuple[tuple[str, SourceSet], ...] = ()
    incomplete_by_transaction: tuple[tuple[str, SourceSet], ...] = ()

    @property
    def incomplete_sources(self) -> SourceSet:
        return union_sources(*(sources for _, sources in self.incomplete_by_currency))

    @property
    def detection_complete(self) -> bool:
        return not self.incomplete_sources

    def sources_for_currency(self, currency: str) -> SourceSet:
        return next(
            (sources for key, sources in self.incomplete_by_currency if key == currency),
            frozenset(),
        )

    def sources_for_transactions(self, transaction_ids: set[str]) -> SourceSet:
        return union_sources(
            *(
                sources
                for transaction_id, sources in self.incomplete_by_transaction
                if transaction_id in transaction_ids
            )
        )

    def is_complete_for(self, currency: str) -> bool:
        return not self.sources_for_currency(currency)


def recurring_income_candidates(
    engine: Engine, household_id: str, *, since: date
) -> RecurringIncomeCandidates:
    """Metadata-preserving recurring-income pipeline.

    Cadence, clustering, median, and transfer exclusion run independently by
    currency, and only when every candidate that could affect that currency is
    readable. Income verdicts do not suppress debit-side transfer evidence.
    """
    from family_cfo_api import income_detection

    def _to_txn(candidate) -> income_detection.IncomeTransaction:
        row = candidate.metadata
        assert candidate.amount is not None
        return income_detection.IncomeTransaction(
            id=row.id,
            occurred_at=row.occurred_at,
            amount_minor=candidate.amount,
            currency=row.currency,
            merchant=row.merchant,
            description=row.description,
            account_name=row.account_name,
            institution=row.institution,
        )

    overrides = repository.list_income_overrides(engine, household_id)
    excluded_ids = {txn_id for txn_id, verdict in overrides.items() if verdict == "exclude"}
    included_ids = {txn_id for txn_id, verdict in overrides.items() if verdict == "include"}
    sources_by_currency: dict[str, set[UnreadableAmountSource]] = {}
    sources_by_transaction: dict[str, set[UnreadableAmountSource]] = {}
    transactions: list[Any] = []

    def _add_source(currency: str, transaction_id: str, source: UnreadableAmountSource) -> None:
        sources_by_currency.setdefault(currency, set()).add(source)
        sources_by_transaction.setdefault(transaction_id, set()).add(source)

    checking = repository.list_income_detection_candidates(engine, household_id, since=since)
    categorized = repository.list_income_categorized_candidates(engine, household_id, since=since)
    categorized_ids = {candidate.metadata.id for candidate in categorized}
    seen: set[str] = set()
    for candidate in [*checking, *categorized]:
        txn_id = candidate.metadata.id
        if txn_id in seen:
            continue
        seen.add(txn_id)
        if txn_id in excluded_ids:
            # An explicit exclusion is stable even when its amount is unreadable.
            # Keep readable rows for the restore UI, but never let an excluded
            # unreadable row make the inference pipeline incomplete.
            if candidate.amount is not None:
                transactions.append(_to_txn(candidate))
            continue
        if txn_id in categorized_ids:
            included_ids.add(txn_id)
        if candidate.amount is None:
            assert candidate.incomplete_source is not None
            _add_source(
                candidate.metadata.currency, txn_id, candidate.incomplete_source
            )
        else:
            transactions.append(_to_txn(candidate))

    outflows_by_currency: dict[str, dict[int, list[date]]] = {}
    outflow_candidates = repository.list_household_outflow_candidates(
        engine, household_id, since=since
    )
    for candidate in outflow_candidates:
        txn_id = candidate.metadata.id
        currency = candidate.metadata.currency
        if candidate.amount is None:
            assert candidate.incomplete_source is not None
            _add_source(currency, txn_id, candidate.incomplete_source)
        else:
            outflows_by_currency.setdefault(currency, {}).setdefault(
                candidate.amount, []
            ).append(candidate.metadata.occurred_at)

    filtered_transactions: list[Any] = []
    candidates: list[Any] = []
    currencies = {transaction.currency for transaction in transactions}
    for currency in currencies:
        currency_transactions = [
            transaction for transaction in transactions if transaction.currency == currency
        ]
        if sources_by_currency.get(currency):
            # A readable-only rerun is not a stability proof. Preserve readable
            # evidence and explicit inclusions, but suppress inferred sources.
            filtered_transactions.extend(currency_transactions)
            continue
        filtered = [
            transaction
            for transaction in currency_transactions
            if transaction.id in included_ids
            or not income_detection.is_internal_transfer(
                transaction, outflows_by_currency.get(currency, {})
            )
        ]
        filtered_transactions.extend(filtered)
        candidates.extend(
            income_detection.detect_income_sources(filtered, excluded_ids=excluded_ids)
        )

    return RecurringIncomeCandidates(
        filtered_transactions,
        candidates,
        included_ids,
        excluded_ids,
        tuple(
            sorted(
                (currency, frozenset(sources))
                for currency, sources in sources_by_currency.items()
            )
        ),
        tuple(
            sorted(
                (transaction_id, frozenset(sources))
                for transaction_id, sources in sources_by_transaction.items()
            )
        ),
    )


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
    # #30: "statement" when the figure came from a recorded statement (exact),
    # "estimate" otherwise. Carried from the timeline item so the outlook can
    # say WHICH card amounts are exact instead of hedging about all of them.
    source: str = "estimate"


@dataclass(frozen=True, slots=True)
class CashOutlook:
    starting_cash_minor: int
    events: list[OutlookEvent]  # date order; same-day outflows before inflows
    ending_cash_minor: int | None
    lowest_minor: int | None
    lowest_date: date | None
    expected_income: Qualified[int] | None
    income_projection: Qualified[None]
    obligations: Qualified[int]
    horizon_days: int

    @property
    def expected_income_minor(self) -> int:
        return self.expected_income.value if self.expected_income is not None else 0

    @property
    def obligations_minor(self) -> int:
        return self.obligations.value
    # ADR 0069: the RSU sell-by runway. First day the projected balance goes
    # negative, and the last day to start an RSU sale with the household's
    # required notice (4 business days: trade + settle + transfer). None when
    # the horizon stays covered.
    first_shortfall_date: date | None = None
    sell_by_date: date | None = None


#: Business days of notice a household needs to turn RSUs into cash in the
#: bank: place the sale, T+1/T+2 settlement, then the ACH out (ADR 0069).
RSU_SALE_NOTICE_BUSINESS_DAYS = 4


def business_days_before(day: date, count: int) -> date:
    """Step back `count` business days (weekends skipped; market holidays are
    not modeled — documented limitation in ADR 0069, so the notice errs on
    acting a day early around holidays rather than late)."""
    current = day
    remaining = count
    while remaining > 0:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


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
    today = today or household_clock.today_for_household(engine, household_id)
    horizon = today + timedelta(days=horizon_days)

    # --- Outflows: the payment timeline's items, projected over the window.
    timeline = payment_timeline(
        engine, household_id, currency, today=today, window_days=horizon_days
    )
    bill_frequency = {
        bill.id: bill.frequency for bill in repository.list_bills(engine, household_id)
    }
    events: list[OutlookEvent] = []
    obligation_sources: set[UnreadableAmountSource] = set()
    for item in timeline.items:
        if item.due_date is None:
            # An unreadable possible payment leaves even an undated obligation's
            # membership uncertain. It cannot become a calendar event, but its
            # provenance must still invalidate the obligation subtotal/runway.
            obligation_sources.update(item.incomplete_sources)
            continue  # undated item: nothing honest to place on the calendar
        # Overdue items claim cash immediately; everything else on its due date.
        first = max(item.due_date, today)
        if first > horizon:
            continue
        if item.status == "unknown" or item.amount_minor is None:
            obligation_sources.update(item.incomplete_sources)
            continue
        events.append(
            OutlookEvent(
                occurred_on=first, name=item.name,
                amount_minor=-item.amount_minor, kind=item.kind,
                source=item.source,
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
                    # source defaults to "estimate": a PROJECTED occurrence is a
                    # repeat of the cadence, never a figure read off a statement,
                    # even when the first occurrence was.
                )
            )
            occurrence = _step(occurrence, frequency)

    # --- Inflows: recurring income sources, stepped forward from their last
    # sighting. Detection needs 2–3 consistent sightings, so a brand-new job
    # won't project until it has history — honest, if conservative.
    since = today - timedelta(days=_INCOME_DETECTION_WINDOW_DAYS)
    income_detection = recurring_income_candidates(engine, household_id, since=since)
    for candidate in income_detection.candidates:
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
    obligation_value = -sum(event.amount_minor for event in events if event.amount_minor < 0)
    obligations = Qualified(obligation_value, frozenset(obligation_sources))
    income_sources = income_detection.sources_for_currency(currency)
    income_projection = Qualified(None, income_sources)
    expected_income = (
        Qualified.complete(sum(event.amount_minor for event in events if event.amount_minor > 0))
        if not income_sources
        else None
    )
    dependency_sources = union_sources(obligations, income_projection)
    running: int | None = None
    lowest: int | None = None
    lowest_date: date | None = None
    first_shortfall: date | None = None
    if not dependency_sources:
        running = starting
        lowest = starting
        for event in events:
            running += event.amount_minor
            if running < lowest:
                lowest = running
                lowest_date = event.occurred_on
            if running < 0 and first_shortfall is None:
                first_shortfall = event.occurred_on
        if starting < 0 and first_shortfall is None:
            first_shortfall = today
    return CashOutlook(
        starting_cash_minor=starting,
        events=events,
        ending_cash_minor=running,
        lowest_minor=lowest,
        lowest_date=lowest_date,
        expected_income=expected_income,
        income_projection=income_projection,
        obligations=obligations,
        horizon_days=horizon_days,
        first_shortfall_date=first_shortfall,
        sell_by_date=(
            business_days_before(first_shortfall, RSU_SALE_NOTICE_BUSINESS_DAYS)
            if first_shortfall is not None
            else None
        ),
    )


# --- The month's spending plan: "left to spend" (M113, ADR 0027) ------------


@dataclass(frozen=True, slots=True)
class SpendingPlan:
    """The Simplifi-style month plan: expected income minus what's already
    spent and what's still committed = left to spend this month."""

    month: str  # "YYYY-MM"
    income_received: Qualified[int]
    income_projected: Qualified[int] | None
    income_projection: Qualified[None]
    expected_income: Qualified[int] | None
    spent: Qualified[int]
    bills_remaining: Qualified[int]
    account_obligations_minor: int  # mortgage/loan/lease payments for the month
    planned_savings_minor: int  # goals' declared monthly contributions (M118)
    left: Qualified[int] | None
    per_day: Qualified[int] | None
    days_remaining: int  # including today

    # Compatibility views for existing complete-only service consumers. The API
    # never uses these; its qualified/null shape is assembled from the fields above.
    @property
    def income_received_minor(self) -> int:
        return self.income_received.value

    @property
    def income_projected_minor(self) -> int:
        return self.income_projected.value if self.income_projected is not None else 0

    @property
    def expected_income_minor(self) -> int:
        return self.expected_income.value if self.expected_income is not None else 0

    @property
    def spent_minor(self) -> int:
        return self.spent.value

    @property
    def bills_remaining_minor(self) -> int:
        return self.bills_remaining.value

    @property
    def left_minor(self) -> int:
        return self.left.value if self.left is not None else 0

    @property
    def per_day_minor(self) -> int:
        return self.per_day.value if self.per_day is not None else 0


def qualified_income_deposits_between(
    engine: Engine, household_id: str, currency: str, start: date, end: date
) -> QualifiedRows[Any]:
    """The individual deposits behind ``income_received_between`` — who paid,
    into which account, when — oldest first. The advisor needs the rows, not
    just the sum, to answer "what made up my income that month?" (user report
    2026-07-25: the month tool only knew the aggregate)."""
    since = min(start, date.today()) - timedelta(days=_INCOME_DETECTION_WINDOW_DAYS)
    detection = recurring_income_candidates(engine, household_id, since=since)
    counted = {
        transaction.id
        for candidate in detection.candidates
        for transaction in candidate.transactions
    } | detection.included_ids
    rows = sorted(
        (
            t
            for t in detection.transactions
            if start <= t.occurred_at <= end
            and t.currency == currency
            and t.id in counted
            and t.id not in detection.excluded_ids
        ),
        key=lambda t: t.occurred_at,
    )
    return QualifiedRows(tuple(rows), detection.sources_for_currency(currency))


def income_deposits_between(
    engine: Engine, household_id: str, currency: str, start: date, end: date
) -> list[Any]:
    """Compatibility projection for Item 3 advisor work; drops no values."""
    return list(
        qualified_income_deposits_between(
            engine, household_id, currency, start, end
        ).rows
    )


def income_received_between(
    engine: Engine, household_id: str, currency: str, start: date, end: date
) -> Qualified[int]:
    """Actual income landed in [start, end] (minor units): the deposits the
    income analysis counts (ADR 0054 — detection, not categorization, is how
    this product knows pay). The Income-category sum alone reads 0 for a
    household that never hand-files paychecks (M-yearly bug: the year view
    showed USD 0 income against 53 payroll deposits)."""
    deposits = qualified_income_deposits_between(
        engine, household_id, currency, start, end
    )
    return Qualified(
        sum(transaction.amount_minor for transaction in deposits.rows),
        deposits.incomplete_sources,
    )


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
    today = today or household_clock.today_for_household(engine, household_id)
    month_start = today.replace(day=1)
    month_end = add_months(month_start, 1) - timedelta(days=1)

    # --- Income: received this month + projected through month end.
    since = today - timedelta(days=_INCOME_DETECTION_WINDOW_DAYS)
    income_detection = recurring_income_candidates(
        engine, household_id, since=since
    )
    counted_ids = {
        transaction.id
        for candidate in income_detection.candidates
        for transaction in candidate.transactions
    } | income_detection.included_ids
    received_value = sum(
        t.amount_minor
        for t in income_detection.transactions
        if month_start <= t.occurred_at <= today
        and t.currency == currency
        and t.id in counted_ids
        and t.id not in income_detection.excluded_ids
    )
    income_sources = income_detection.sources_for_currency(currency)
    received = Qualified(received_value, income_sources)
    projected_value = 0
    for candidate in income_detection.candidates:
        if candidate.currency != currency or not candidate.transactions:
            continue
        payday = max(t.occurred_at for t in candidate.transactions)
        while payday <= today:
            payday = _step(payday, candidate.frequency)
        while payday <= month_end:
            projected_value += candidate.typical_amount_minor
            payday = _step(payday, candidate.frequency)

    # --- Already out: month-to-date spending (see docstring for what counts).
    spent = repository.sum_spending(engine, household_id, month_start, today, currency)

    # --- Still committed: unpaid bills due through month end...
    timeline = payment_timeline(
        engine, household_id, currency,
        today=today, window_days=max((month_end - today).days, 0),
    )
    bills_remaining_value = sum(
        item.amount_minor
        for item in timeline.items
        if item.kind == "bill"
        and item.status in ("overdue", "due_soon", "upcoming")
        and item.due_date is not None
        and item.due_date <= month_end
        and item.amount_minor is not None
    )
    bills_remaining_sources = union_sources(
        *(
            item.incomplete_sources
            for item in timeline.items
            if item.kind == "bill"
            and item.due_date is not None
            and item.due_date <= month_end
        )
    )
    bills_remaining = Qualified(bills_remaining_value, bills_remaining_sources)
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

    projected = Qualified.complete(projected_value) if not income_sources else None
    income_projection = Qualified(None, income_sources)
    expected_income = (
        Qualified.complete(received.value + projected.value)
        if received.is_complete and projected is not None
        else None
    )
    dependency_sources = union_sources(received, income_projection, spent, bills_remaining)
    left = per_day = None
    days_remaining = (month_end - today).days + 1
    if not dependency_sources and expected_income is not None:
        left_value = (
            expected_income.value
            - spent.value
            - bills_remaining.value
            - account_obligations
            - planned_savings
        )
        left = Qualified.complete(left_value)
        per_day = Qualified.complete(left_value // days_remaining if left_value > 0 else 0)
    return SpendingPlan(
        month=f"{today.year}-{today.month:02d}",
        income_received=received,
        income_projected=projected,
        income_projection=income_projection,
        expected_income=expected_income,
        spent=spent,
        bills_remaining=bills_remaining,
        account_obligations_minor=account_obligations,
        planned_savings_minor=planned_savings,
        left=left,
        per_day=per_day,
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
    "monthly": 10, "quarterly": 15, "semiannual": 18, "annual": 20,
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
    # "matched" = found by the merchant+window matcher; "linked" = the user
    # pointed the bill at this transaction (the link wins over the matcher).
    source: str = "matched"
    link_id: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentTimelineItem:
    id: str  # bill id, or liability account id
    kind: str  # "bill" | "credit_card" | "mortgage" | "loan" | "lease"
    name: str
    amount_minor: int | None  # unreadable statement-backed amount is absent
    currency: str
    due_date: date | None  # None = we couldn't infer one ("no_date")
    status: str  # "overdue" | "due_soon" | "upcoming" | "paid" | "no_date"
    paid: TimelinePayment | None
    # #11: "statement" when the figure and date come from an uploaded/entered
    # statement (exact), "estimate" otherwise (running balance + inferred day).
    # The UI and the advisor must not present an estimate as an exact amount.
    source: str = "estimate"
    statement_id: str | None = None
    incomplete_sources: SourceSet = frozenset()


@dataclass(frozen=True, slots=True)
class PaymentTimeline:
    items: list[PaymentTimelineItem]
    due_total: Qualified[int]  # overdue + due-soon, the "what needs paying now" number
    liquid_minor: int
    covered: bool | None
    window_days: int

    @property
    def due_total_minor(self) -> int:
        return self.due_total.value


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
    bills: list[repository.RecurringRecord],
    liability_accounts: list[repository.AccountRecord],
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
    bill: repository.RecurringRecord,
    outflows: list[repository.TransactionRecord],
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
    transactions: list[repository.TransactionRecord],
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


_TIMELINE_STATUS_ORDER = {
    "overdue": 0, "due_soon": 1, "unknown": 2,
    "no_date": 3, "paid": 4, "upcoming": 5,
}


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
    today = today or household_clock.today_for_household(engine, household_id)
    lookback_start = today - timedelta(days=_TIMELINE_LOOKBACK_DAYS)
    candidates = repository.iter_transaction_amount_candidates(
        engine, household_id, start=lookback_start, end=today
    )
    by_account: dict[str, list[repository.TransactionRecord]] = {}
    outflows: list[repository.TransactionRecord] = []
    unreadable = []
    for candidate in candidates:
        metadata = candidate.metadata
        if candidate.amount is None:
            unreadable.append(candidate)
            continue
        txn = repository.TransactionRecord(
            id=metadata.id,
            account_id=metadata.account_id,
            occurred_at=metadata.occurred_at,
            amount_minor=candidate.amount,
            currency=metadata.currency,
            merchant=metadata.merchant,
            category=metadata.category,
            description=metadata.description,
            category_id=metadata.category_id,
        )
        by_account.setdefault(txn.account_id, []).append(txn)
        if txn.amount_minor < 0:
            outflows.append(txn)
    outflows.sort(key=lambda transaction: transaction.occurred_at, reverse=True)

    def _potential_bill_sources(
        bill: repository.RecurringRecord, window_start: date, window_end: date
    ) -> SourceSet:
        from family_cfo_api import bill_detection

        bill_key = bill_detection.normalize_merchant(bill.name)
        sources: set[UnreadableAmountSource] = set()
        for candidate in unreadable:
            row = candidate.metadata
            if not (window_start <= row.occurred_at <= window_end):
                continue
            candidate_key = bill_detection.normalize_merchant(
                row.merchant
            ) or bill_detection.normalize_merchant(row.description)
            if _keys_match(bill_key, candidate_key):
                assert candidate.incomplete_source is not None
                sources.add(candidate.incomplete_source)
        return frozenset(sources)

    def _potential_account_payment_sources(account_id: str) -> SourceSet:
        sources: set[UnreadableAmountSource] = set()
        for candidate in unreadable:
            row = candidate.metadata
            label = (row.merchant or row.description or "").lower()
            if row.account_id == account_id and any(word in label for word in _PAYMENT_LABEL_WORDS):
                assert candidate.incomplete_source is not None
                sources.add(candidate.incomplete_source)
        return frozenset(sources)

    horizon = today + timedelta(days=window_days)
    items: list[PaymentTimelineItem] = []

    links = {
        (link.bill_id, link.due_date): link
        for link in repository.list_bill_payment_links(engine, household_id)
    }

    def _linked_payment(
        bill_id: str, due: date
    ) -> tuple[TimelinePayment | None, SourceSet, bool]:
        """Return a linked receipt, unreadable source, and whether a link exists."""
        link = links.get((bill_id, due))
        if link is None:
            return None, frozenset(), False
        candidate = repository.get_transaction_amount_candidate(
            engine, household_id, link.transaction_id
        )
        if candidate is None:
            return None, frozenset(), True
        if candidate.amount is None:
            assert candidate.incomplete_source is not None
            return None, frozenset({candidate.incomplete_source}), True
        row = candidate.metadata
        return (
            TimelinePayment(
                transaction_id=row.id,
                occurred_at=row.occurred_at,
                amount_minor=abs(candidate.amount),
                label=row.merchant or row.description or "Payment",
                source="linked",
                link_id=link.id,
            ),
            frozenset(),
            True,
        )

    def _resolve_bill_payment(
        bill: repository.RecurringRecord,
        due: date,
        window_start: date,
        window_end: date,
    ) -> tuple[TimelinePayment | None, SourceSet]:
        linked, linked_sources, link_exists = _linked_payment(bill.id, due)
        if link_exists:
            return linked, linked_sources
        matched = _find_bill_payment(bill, outflows, window_start, window_end)
        if matched is not None:
            return matched, frozenset()
        return None, _potential_bill_sources(bill, window_start, window_end)

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
        incomplete_sources: SourceSet = frozenset()
        status: str
        due: date | None
        if prev_due >= today - timedelta(days=grace):
            # A due date just passed: paid near it, or genuinely overdue.
            paid, incomplete_sources = _resolve_bill_payment(
                bill,
                prev_due,
                prev_due - timedelta(days=5),
                min(prev_due + timedelta(days=grace), today),
            )
            if paid is not None:
                status, due = "paid", next_due
            elif incomplete_sources:
                status, due = "unknown", prev_due
            else:
                status, due = "overdue", prev_due
        else:
            # Otherwise the question is the upcoming occurrence — possibly
            # already settled early by autopay.
            paid, incomplete_sources = _resolve_bill_payment(
                bill, next_due, next_due - timedelta(days=5), today
            )
            if paid is not None:
                status, due = "paid", next_due
            elif incomplete_sources:
                status, due = "unknown", next_due
            elif next_due <= horizon:
                status, due = "due_soon", next_due
            else:
                status, due = "upcoming", next_due
        items.append(
            PaymentTimelineItem(
                id=bill.id, kind="bill", name=bill.name,
                amount_minor=bill.amount_minor, currency=currency,
                due_date=due, status=status, paid=paid,
                incomplete_sources=incomplete_sources,
            )
        )

    # --- Credit cards: the payment is the (pay-in-full) balance; the due day is
    # inferred from the card's own payment history.
    balances = {b.account_id: b for b in repository.list_account_balances(engine, household_id)}
    # #11: the newest UNPAID statement per card. It carries the exact amount and
    # a real due date, so it replaces the balance estimate entirely — showing
    # both would list the same money twice.
    statements_by_account = {
        candidate.metadata.account_id: candidate
        for candidate in repository.list_authoritative_statement_balance_candidates(
            engine, household_id, currency=currency
        )
    }

    for account in repository.list_liability_accounts(engine, household_id):
        if account.currency != currency or account.account_type != "credit_card":
            continue
        payments = _account_payments(by_account.get(account.id, []))
        statement_candidate = statements_by_account.get(account.id)
        if statement_candidate is not None:
            statement = statement_candidate.metadata
            # A payment clearing the card for roughly the statement amount marks
            # the cycle paid — same tolerance idea as bill matching, since a
            # credit or a rounding adjustment shifts the exact figure.
            account_sources = _potential_account_payment_sources(account.id)
            if statement_candidate.amount is None:
                match = None
                status = "unknown"
                incomplete_sources = statement_candidate.incomplete_sources
            else:
                match = _statement_payment(
                    payments, statement_candidate.amount, statement.due_date
                )
                if match is not None:
                    status = "paid"
                    incomplete_sources = frozenset()
                elif account_sources:
                    status = "unknown"
                    incomplete_sources = account_sources
                else:
                    status = _status_for(statement.due_date, today=today, horizon=horizon)
                    incomplete_sources = frozenset()
            items.append(
                PaymentTimelineItem(
                    id=account.id,
                    kind="credit_card",
                    name=account.name,
                    amount_minor=statement_candidate.amount,
                    currency=currency,
                    due_date=statement.due_date,
                    status=status,
                    paid=match,
                    source="statement",
                    statement_id=statement.id,
                    incomplete_sources=incomplete_sources,
                )
            )
            continue

        balance = balances.get(account.id)
        owed = -balance.balance_minor if balance is not None and balance.balance_minor < 0 else 0
        if owed <= 0 and not payments:
            continue  # inactive card: nothing owed, no history
        items.append(
            _liability_item(
                account.id, "credit_card", account.name, owed, currency,
                payments, today=today, horizon=horizon,
                stored_due_date=account.next_payment_due_date,
                incomplete_sources=_potential_account_payment_sources(account.id),
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
                incomplete_sources=_potential_account_payment_sources(obligation.account_id),
            )
        )

    items.sort(
        key=lambda i: (_TIMELINE_STATUS_ORDER.get(i.status, 9), i.due_date or date.max)
    )

    due_total_value = sum(
        item.amount_minor
        for item in items
        if item.status in ("overdue", "due_soon") and item.amount_minor is not None
    )
    due_sources = union_sources(
        *(
            item.incomplete_sources
            for item in items
            if item.due_date is None or item.due_date <= horizon
        )
    )
    due_total = Qualified(due_total_value, due_sources)
    liquid = sum(
        balance.balance_minor
        for balance in balances.values()
        if balance.account_type in LIQUID_ACCOUNT_TYPES and balance.currency == currency
    )
    return PaymentTimeline(
        items=items,
        due_total=due_total,
        liquid_minor=liquid,
        covered=liquid >= due_total_value if due_total.is_complete else None,
        window_days=window_days,
    )


def _status_for(due_date: date, *, today: date, horizon: date) -> str:
    """#11: a statement's due date is KNOWN (read off the statement), never
    inferred — so unlike a guessed card date it can legitimately be overdue."""
    if due_date < today:
        return "overdue"
    if due_date <= horizon:
        return "due_soon"
    return "upcoming"


def _statement_payment(
    payments: list[TimelinePayment], amount_minor: int, due_date: date
) -> TimelinePayment | None:
    """The payment that cleared a statement cycle, if one has posted.

    Matched on amount within the usual tolerance (a statement credit or a
    rounding adjustment shifts the exact figure) and on timing: a payment for
    THIS cycle lands somewhere between the statement closing and shortly after
    the due date. A payment far outside that window belongs to another cycle.
    """
    window_start = due_date - timedelta(days=45)
    # Monthly grace: the same allowance a monthly bill gets before we stop
    # claiming it is late.
    window_end = due_date + timedelta(days=_GRACE_DAYS_BY_FREQUENCY["monthly"])
    for payment in payments:
        if not (window_start <= payment.occurred_at <= window_end):
            continue
        if abs(payment.amount_minor - amount_minor) <= amount_minor * _MATCH_AMOUNT_TOLERANCE:
            return payment
    return None


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
    incomplete_sources: SourceSet = frozenset(),
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
            currency=currency, due_date=None,
            status="unknown" if incomplete_sources else "no_date", paid=None,
            incomplete_sources=incomplete_sources,
        )
    recently_paid = last is not None and (today - last.occurred_at).days <= 31
    if incomplete_sources:
        status = "unknown"
    elif next_due <= horizon:
        status = "due_soon"
    elif recently_paid:
        status = "paid"
    else:
        status = "upcoming"
    return PaymentTimelineItem(
        id=account_id, kind=kind, name=name, amount_minor=amount_minor,
        currency=currency, due_date=next_due, status=status,
        paid=last if status == "paid" else None,
        incomplete_sources=incomplete_sources,
    )


@dataclass(frozen=True, slots=True)
class SubscriptionForecastItem:
    name: str
    amount_minor: int
    currency: str
    next_charge: date


@dataclass(frozen=True, slots=True)
class SubscriptionForecast:
    items: list[SubscriptionForecastItem]
    total: Qualified[Money] | None
    detection: Qualified[None]


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
) -> SubscriptionForecast:
    """Recurring charges in the 'Subscriptions' category whose NEXT occurrence lands
    within the horizon and isn't yet paid this cycle (M109, ADR 0020). Reserved the
    'bill way': only the upcoming in-window charge, never a monthly total, so a
    charge already deducted from liquid is never double-counted."""
    from family_cfo_api import bill_detection

    today = today or household_clock.today_for_household(engine, household_id)
    category_id = _subscriptions_category_id(engine, household_id)
    if category_id is None:
        return SubscriptionForecast(
            [], Qualified.complete(Money.zero(currency)), Qualified.complete(None)
        )

    since = today - timedelta(days=bill_detection.LOOKBACK_DAYS)
    amount_candidates = list(
        repository.iter_transaction_amount_candidates(
            engine,
            household_id,
            start=since,
            end=today,
            currency=currency,
            category_id=category_id,
        )
    )
    sources = union_sources(
        *(candidate.incomplete_sources for candidate in amount_candidates)
    )
    if sources:
        return SubscriptionForecast([], None, Qualified(None, sources))
    detection = [
        bill_detection.DetectionTransaction(
            occurred_at=candidate.metadata.occurred_at,
            amount_minor=candidate.amount,
            currency=candidate.metadata.currency,
            merchant=candidate.metadata.merchant,
            description=candidate.metadata.description,
        )
        for candidate in amount_candidates
        if candidate.amount is not None and candidate.amount < 0
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
    return SubscriptionForecast(items, Qualified.complete(total), Qualified.complete(None))


@dataclass(frozen=True, slots=True)
class CommittedSavingsForecast:
    total: Qualified[Money]
    items: list[tuple[str, Money, date]]
    detection: Qualified[None]


def committed_savings_in_window(
    engine, household_id: str, currency: str, *, today: date, horizon_days: int
) -> CommittedSavingsForecast:
    """#5: DECLARED savings contributions whose next occurrence lands within the
    horizon — money the household has committed to setting aside. Detected-only
    candidates are excluded: only what the family confirmed counts as committed
    (the #203 durability rule). Returns (total, items) where each item is
    (name, amount, next_date)."""
    from family_cfo_api import savings_detection

    found = savings_detection.qualified_detect_for_household(
        engine, household_id, today=today
    )

    horizon = today + timedelta(days=horizon_days)
    # Cadences that fire at least once inside a ~monthly horizon. A declared
    # contribution with no synced arrivals (the common 529 case, #201) has no
    # anchor day, so its projected next_expected sits a full period out — for
    # these we count one occurrence as due, since a monthly-or-more-frequent
    # commitment definitely lands within the window.
    at_least_monthly = {"weekly", "biweekly", "semimonthly", "monthly"}
    total = Money.zero(currency)
    items: list[tuple[str, Money, date]] = []
    for c in found.value:
        # Only the household's own word counts as committed (#203 durability).
        if not c.declared or c.currency != currency:
            continue
        anchored = c.occurrences > 0
        if anchored:
            in_window = c.next_expected is not None and c.next_expected <= horizon
        else:
            in_window = c.frequency in at_least_monthly
        if not in_window:
            continue
        amount = Money(c.amount_minor, c.currency)
        total += amount
        # Show the projected date when anchored; otherwise the horizon end, so
        # the label reads honestly ("expected within the window").
        due = c.next_expected if anchored and c.next_expected else horizon
        items.append((c.destination_name, amount, due))
    items.sort(key=lambda it: it[2])
    return CommittedSavingsForecast(
        total=Qualified.complete(total),
        items=items,
        detection=Qualified(None, found.incomplete_sources),
    )


@dataclass(frozen=True, slots=True)
class SafeToSpendComputation:
    liquid_balance: Money
    emergency_fund_reserved: Money
    bills_due: Money
    minimum_debt_payments: Money
    credit_card_payments: Qualified[int] | None
    credit_card_items: tuple[tuple[str, Money], ...]
    subscription_forecast: Qualified[int] | None
    subscription_forecast_items: tuple[SubscriptionForecastItem, ...]
    subscription_detection: Qualified[None]
    committed_savings: Qualified[int] | None
    committed_savings_items: tuple[tuple[str, Money, date], ...]
    savings_detection: Qualified[None]
    committed_total: Qualified[int] | None
    safe_to_spend: Qualified[int] | None
    total_debt: Money
    committed_savings_reserved: bool


def compute_safe_to_spend(
    engine: Engine,
    household_id: str,
    currency: str,
    *,
    horizon_days: int = SAFE_TO_SPEND_HORIZON_DAYS,
    today: date | None = None,
) -> tuple[CalculationAttempt, str]:
    """What the family can actually spend today, net of everything already owed.

    The advisor used to answer "how much can I spend?" by subtracting the
    emergency fund from liquid cash and calling the rest discretionary. That
    ignored every bill about to land and every minimum debt payment due, which
    overstated the answer by precisely the amount the family owed.
    """
    resolved_today = today or household_clock.today_for_household(engine, household_id)
    balances = repository.list_account_balances(engine, household_id)
    # #152: every loop below already filters on `currency`; the partition exists
    # so the figure can DISCLOSE the out-of-base accounts it would have counted.
    partition = partition_balances_by_currency(balances, currency, eligible=_touches_safe_to_spend)
    # The obligation loops below add what they skip (#152 review): a foreign
    # lease with a recorded payment has no negative balance, so only the loop
    # that would have reserved its payment knows it was left out.
    excluded = {a.account_id: a for a in partition.excluded_accounts}
    balance_by_id = {b.account_id: b.balance_minor for b in balances}
    account_name_by_id = {b.account_id: b.name for b in balances}
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
        engine, household_id, currency, today=resolved_today, window_days=horizon_days
    ):
        if bill.amount.currency == currency:
            bills_due += bill.amount

    # M109 (ADR 0020): recurring subscriptions' next in-window charge — reserved the
    # 'bill way' so an already-paid charge is never double-counted.
    subscription = subscription_forecast(
        engine, household_id, currency, today=resolved_today, horizon_days=horizon_days
    )
    subscription_forecast_total = (
        subscription.total.value if subscription.total is not None else Money.zero(currency)
    )

    # Every liability the household carries, as a positive amount. Not subtracted
    # (a balance is not due this month) but reported: a safe-to-spend figure
    # said beside a silent five-figure card debt is true and still misleading.
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
        if debt.minimum_payment_minor is None:
            continue
        if debt.currency != currency:
            if (
                debt.account_id not in bill_covered_accounts
                and debt.account_type not in repository.RETIREMENT_LOAN_TYPES
            ):
                excluded.setdefault(
                    debt.account_id,
                    ExcludedAccount(
                        debt.account_id,
                        debt.name,
                        debt.account_type,
                        Money(-debt.balance_owed_minor, debt.currency),
                    ),
                )
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
            account.minimum_payment_minor is None
            or account.id in modeled_ids
            or account.account_type == "credit_card"
            or account.account_type in repository.RETIREMENT_LOAN_TYPES
        ):
            continue
        if account.currency != currency:
            excluded.setdefault(
                account.id,
                ExcludedAccount(
                    account.id,
                    account.name,
                    account.account_type,
                    Money(balance_by_id.get(account.id, 0), account.currency),
                ),
            )
            continue
        modeled_ids.add(account.id)
        minimum_debt_payments += Money(account.minimum_payment_minor, account.currency)

    # M96: a household that pays its cards in full each month has its whole card
    # balance about to leave liquid cash — commit the full balances, not just the
    # minimum, and treat those cards as modeled so they aren't warned as unrecorded.
    household = repository.get_household(engine, household_id)
    credit_card_payments = Money.zero(currency)
    credit_card_items: list[tuple[str, Money]] = []
    credit_card_sources: set[UnreadableAmountSource] = set()
    # #11: an uploaded statement is a PRECISE claim — the amount actually due on
    # a known date — so it REPLACES the running-balance estimate for that card
    # rather than adding to it. Counting both would charge the household twice
    # for the same money, since the statement balance is part of the balance.
    statement_horizon = resolved_today + timedelta(days=horizon_days)
    statement_candidates = repository.list_authoritative_statement_balance_candidates(
        engine,
        household_id,
        currency=currency,
        due_on_or_before=statement_horizon,
    )
    statement_account_ids: set[str] = set()
    for candidate in statement_candidates:
        account_id = candidate.metadata.account_id
        statement_account_ids.add(account_id)
        modeled_ids.add(account_id)
        if candidate.amount is None:
            assert candidate.incomplete_source is not None
            credit_card_sources.add(candidate.incomplete_source)
            continue
        amount = Money(candidate.amount, currency)
        credit_card_payments += amount
        credit_card_items.append(
            (account_name_by_id.get(account_id, "Credit card"), amount)
        )

    if household is not None and household.credit_cards_paid_in_full:
        for balance in balances:
            if (
                balance.account_type == "credit_card"
                and balance.currency == currency
                and balance.balance_minor < 0
                # Already counted precisely from its statement.
                and balance.account_id not in statement_account_ids
            ):
                amount = Money(-balance.balance_minor, balance.currency)
                credit_card_payments += amount
                credit_card_items.append((balance.name, amount))
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

    # #5: committed savings due in this window — reserved only if the household
    # asked; otherwise the caller surfaces it beside the figure.
    committed_savings = committed_savings_in_window(
        engine, household_id, currency, today=resolved_today, horizon_days=horizon_days
    )
    committed_savings_total = committed_savings.total.value
    reserve_savings = bool(household is not None and household.reserve_committed_savings)

    credit_card_qualified = Qualified(
        credit_card_payments.amount_minor, frozenset(credit_card_sources)
    )
    credit_card_public = (
        credit_card_qualified
        if statement_account_ids or credit_card_payments.amount_minor > 0
        else None
    )
    # Automatic subscription forecasting is all-or-nothing. Union the amount
    # and detection source sets so the public field and status cannot disagree.
    subscription_sources = union_sources(subscription.total, subscription.detection)
    subscription_detection = Qualified(None, subscription_sources)
    subscription_public = (
        Qualified.complete(subscription_forecast_total.amount_minor)
        if not subscription_sources
        and subscription.total is not None
        and subscription_forecast_total.amount_minor > 0
        else None
    )
    committed_public = (
        Qualified.complete(committed_savings_total.amount_minor)
        if committed_savings_total.amount_minor > 0
        else None
    )
    decision_sources = union_sources(credit_card_qualified, subscription_detection)

    if not decision_sources:
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
                committed_savings=committed_savings_total,
                reserve_savings=reserve_savings,
            )
        )
        result.outputs["committed_savings_reserved"] = reserve_savings
        attempt = CalculationAttempt.complete(result)
        committed_total = Qualified.complete(
            result.outputs["committed_total"].amount_minor
        )
        safe_value = Qualified.complete(
            result.outputs["safe_to_spend"].amount_minor
        )
    else:
        attempt = CalculationAttempt(
            calculation_type="safe_to_spend",
            inputs={"incomplete_amount_count": len(decision_sources)},
            assumptions=[],
            warnings=[INCOMPLETE_WARNING],
            outputs={
                "liquid_balance": liquid_balance,
                "emergency_fund_reserved": reserved,
                "bills_due": bills_due,
                "minimum_debt_payments": minimum_debt_payments,
                "credit_card_payments": credit_card_qualified,
                "subscription_forecast": subscription_public,
                "committed_savings": committed_public,
                "committed_total": None,
                "safe_to_spend": None,
                "total_debt": total_debt,
                "committed_savings_reserved": reserve_savings,
            },
            incomplete_sources=decision_sources,
        )
        committed_total = None
        safe_value = None

    attempt.response = SafeToSpendComputation(
        liquid_balance=liquid_balance,
        emergency_fund_reserved=reserved,
        bills_due=bills_due,
        minimum_debt_payments=minimum_debt_payments,
        credit_card_payments=credit_card_public,
        credit_card_items=tuple(credit_card_items),
        subscription_forecast=subscription_public,
        subscription_forecast_items=tuple(subscription.items),
        subscription_detection=subscription_detection,
        committed_savings=committed_public,
        committed_savings_items=tuple(committed_savings.items),
        savings_detection=committed_savings.detection,
        committed_total=committed_total,
        safe_to_spend=safe_value,
        total_debt=total_debt,
        committed_savings_reserved=reserve_savings,
    )
    calculation_id = _persist_attempt_disclosing(
        engine,
        household_id,
        attempt,
        currency,
        list(excluded.values()),
        warnings=fund.warnings,
    )
    return attempt, calculation_id


def compute_purchase_impact(
    engine: Engine, household_id: str, currency: str, price: Money
) -> tuple[CalculationResult, str]:
    monthly_expenses, denominator_excluded = monthly_essential_expenses_with_exclusions(
        engine, household_id, currency
    )
    if not monthly_expenses.is_complete:
        # A recommendation-shaped purchase decision has no honest partial form.
        # Keep this gate inside the service so direct callers cannot bypass it.
        raise SealedAmountUnreadableError(household_id)

    # #152: the same partition net worth uses — a foreign account is left out of
    # the before/after net worth AND the liquid balance, and disclosed once.
    partition = partition_balances_by_currency(
        repository.list_account_balances(engine, household_id),
        currency,
        eligible=_counts_toward_net_worth,
    )
    engine_balances = [
        AccountBalance(b.account_id, b.account_type, Money(b.balance_minor, b.currency))
        for b in partition.in_base
    ]
    net_worth_result = calculate_net_worth(engine_balances, currency)

    liquid_balance = Money.zero(currency)
    for balance in partition.in_base:
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
    top_goal_warning: str | None = None
    if goals:
        top = goals[0]
        if top.currency.upper() == currency.upper():
            top_goal = GoalInput(
                goal_id=top.id,
                name=top.name,
                target=Money(top.target_minor, top.currency),
                current=Money(top.current_minor, top.currency),
            )
        else:
            # #152 review: a goal declared in another currency cannot be measured
            # against a base-currency price (the engine would raise), and
            # skipping it silently would hide the household's top priority.
            top_goal_warning = (
                f"The household's top goal is held in {top.currency.upper()} and is not "
                f"modeled in this {currency} figure; goal amounts are never converted."
            )

    result = calculate_purchase_impact(
        PurchaseImpactInputs(
            price=price,
            net_worth_before=net_worth_result.outputs["net_worth"],
            liquid_balance_before=liquid_balance,
            monthly_essential_expenses=monthly_expenses.value,
            discretionary_cash_flow=(
                cash_flow_result.outputs["monthly_income"] - monthly_expenses.value
            ),
            liability_total=net_worth_result.outputs["liability_total"],
            top_goal=top_goal,
        )
    )
    calculation_id = _persist_disclosing(
        engine,
        household_id,
        result,
        currency,
        _merge_excluded(partition.excluded_accounts, denominator_excluded),
        warnings=(top_goal_warning,),
    )
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
    # Key on (merchant, is_inflow) so an inflow category (an employer RSU deposit
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


def autofile_taxes(
    engine: Engine, household_id: str, actor_user_id: str | None = None
) -> int:
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
        category = repository.create_category(engine, household_id, "Taxes")
        tax_category_id = category.id
        # #63: a category appearing on its own is a state change of its own — say
        # what created it, rather than letting it show up unexplained.
        audit.write_audit(
            engine,
            household_id,
            actor_user_id,
            "category.created",
            "category",
            category.id,
            "Created the “Taxes” category to auto-file tax withholding after a sync",
            undo_token=undo_actions.created("category", category.id),
        )
    return repository.set_transactions_category(engine, household_id, ids, tax_category_id)


def monthly_taxes_total(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> Qualified[Money]:
    """Qualified trailing tax-withholding monthly average."""
    today = today or household_clock.today_for_household(engine, household_id)
    this_month_start = today.replace(day=1)
    window_start = add_months(this_month_start, -INCOME_TRAILING_MONTHS)
    window_end = this_month_start - timedelta(days=1)
    total = repository.sum_taxes(
        engine, household_id, window_start, window_end, currency
    )
    return Qualified(
        Money(total.value // INCOME_TRAILING_MONTHS, currency),
        total.incomplete_sources,
    )


def autofile_all(
    engine: Engine, household_id: str, actor_user_id: str | None = None
) -> tuple[int, int]:
    """M96 rule: keep freshly-imported transactions out of the Categorize queue when
    the system can already tell where they go — interest/dividends recognised as
    income, RSU sell-to-cover as taxes, transfers filed under Transfers, and a known
    merchant reusing the category the user gave it before. Income runs first so an
    "Interest Payment" is recognised as earnings, not swept into transfers. Runs on
    EVERY sync path (manual, initial, and the daily worker). Returns
    (transfers_filed, auto_categorized).

    #63: the run leaves ONE audit row carrying how many transactions it rewrote and
    which rule filed them — category assignment drives every budget and report, so
    a bulk rewrite must not be invisible. ``actor_user_id`` is None for the nightly
    worker: nobody asked for it, so nobody is credited with it.
    """
    # Snapshot what is unfiled BEFORE the rules run, so the audit row can name the
    # exact transactions this run touched — that set is also the undo token.
    was_uncategorized = {
        t.id
        for t in repository.list_transactions(engine, household_id, limit=100_000)
        if t.category_id is None
    }

    income_filed = autofile_income(engine, household_id)
    taxes_filed = autofile_taxes(engine, household_id, actor_user_id)
    transfers_filed = autofile_transfers(engine, household_id)
    auto_categorized = autocategorize_by_history(engine, household_id)
    # M97: surface exact-duplicate charges (same account/date/amount/merchant) for
    # the Review queue so the user can dispute a double-charge.
    repository.flag_possible_duplicates(engine, household_id)

    filed_ids = [
        t.id
        for t in repository.list_transactions(engine, household_id, limit=100_000)
        if t.category_id is not None and t.id in was_uncategorized
    ]
    if filed_ids:
        audit.write_audit(
            engine,
            household_id,
            actor_user_id,
            "transactions.auto_filed",
            "transaction",
            None,
            f"Auto-filed {len(filed_ids)} transactions after sync "
            f"({income_filed} income, {taxes_filed} taxes, {transfers_filed} transfers, "
            f"{auto_categorized} by a known merchant)",
            undo_token=undo_actions.transactions_auto_filed(filed_ids),
        )
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


def goal_funding(
    engine: Engine,
    household_id: str,
    record,
    *,
    current_minor: int,
    today: date | None = None,
):
    """#4: what is actually filling this goal — the linked contributions'
    monthly run-rate and a completion date projected from it.

    Returns (monthly_equivalent_minor, funded_by_records, projected_completion,
    funding_status). Payroll deductions never reach the ledger (#201), so an
    "unfunded" retirement goal may be funded invisibly — the CALLER words that
    caveat; this function reports only what the ledger shows.
    """
    from family_cfo_api import savings_detection

    today = today or household_clock.today_for_household(engine, household_id)
    linked = [
        c
        for c in repository.list_savings_contributions(engine, household_id)
        if c.goal_id == record.id
    ]
    monthly = 0
    for c in linked:
        candidate = savings_detection.ContributionCandidate(
            source_account_id=c.source_account_id,
            destination_account_id=c.destination_account_id,
            destination_name="",
            destination_type="",
            amount_minor=c.amount_minor,
            currency=c.currency,
            frequency=c.frequency,
            occurrences=0,
            last_seen=today,
            next_expected=today,
        )
        monthly += savings_detection.monthly_equivalent_minor(candidate)

    remaining = max(0, record.target_minor - current_minor)
    projected: date | None = None
    if monthly > 0 and remaining > 0:
        months = -(-remaining // monthly)  # ceil
        projected = add_months(today, months)
    elif remaining == 0:
        projected = today

    if monthly == 0:
        status = "unfunded"
    elif record.target_date is None:
        status = "funded_no_date"
    elif projected is not None and projected <= record.target_date:
        status = "on_track"
    else:
        status = "behind"
    return monthly, linked, projected, status


def goal_current_minor(
    engine: Engine,
    household_id: str,
    goal: repository.GoalRecord,
    *,
    base_currency: str | None = None,
) -> int:
    """A goal's real progress. An emergency-fund goal tracks the household's
    DESIGNATED emergency fund (the same figure the Overview's Emergency Fund card
    shows) — otherwise the goal reads $0 while the fund holds real money (M41
    fix). Only when the family has actually earmarked emergency money: with no
    designation there's no live figure to trust, so it falls back to the stored
    current. Every other goal type uses its stored current."""
    if goal.goal_type == "emergency_fund":
        if base_currency is None:
            household = repository.get_household(engine, household_id)
            base_currency = household.base_currency if household is not None else goal.currency
        # #152 review: the fund is a base-currency figure and goal amounts are
        # never converted, so a goal declared in another currency has no live
        # figure to trust — it keeps its stored current, like an undesignated
        # one. (Partitioning by the goal's currency instead produced warnings
        # about every base-currency account, with nowhere to send them.)
        if goal.currency.upper() == base_currency.upper():
            ef = emergency_fund_inputs(engine, household_id, base_currency)
            if ef.using_designations:
                return ef.fund.amount_minor
    return goal.current_minor


INCOME_TRAILING_MONTHS = 12


def monthly_income_total(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> Qualified[Money]:
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

    today = today or household_clock.today_for_household(engine, household_id)
    this_month_start = today.replace(day=1)
    window_start = add_months(this_month_start, -INCOME_TRAILING_MONTHS)
    window_end = this_month_start - timedelta(days=1)
    trailing = repository.sum_income(
        engine, household_id, window_start, window_end, currency
    )
    total += Money(trailing.value // INCOME_TRAILING_MONTHS, currency)
    return Qualified(total, trailing.incomplete_sources)


@dataclass(frozen=True, slots=True)
class ObservedSavingsRate:
    """#6: saving as what the household actually does, from three sources that
    must not overlap:

      transfers  — declared contributions to savings vehicles (#203). Excluded
                   from spending, so they'd otherwise sit in the residual; we
                   pull them out into their own bucket.
      payroll    — pre-tax 401(k)/HSA deductions. Never reach the bank feed, so
                   they are NOT in take-home and never in the residual.
      residual   — take-home minus spending minus transfers: money that came in
                   and wasn't spent or moved anywhere identifiable.

    total = transfers + payroll + residual = payroll + (take_home - spending).
    gross = take_home + payroll. percent = total / gross.
    """

    percent: int | None
    gross_monthly: Qualified[int] | None
    take_home_monthly: Qualified[int]
    spending_monthly: Qualified[int]
    transfers_monthly: Qualified[int] | None
    transfer_detection: Qualified[None]
    payroll_monthly_minor: int
    residual_monthly: Qualified[int] | None
    total_saved_monthly: Qualified[int] | None
    # Which sources contributed real numbers, for the honesty note.
    has_payroll_profile: bool
    has_declared_transfers: bool


def observed_savings_rate(
    engine, household_id: str, currency: str, *, today: date | None = None
) -> ObservedSavingsRate:
    from family_cfo_api import savings_detection

    today = today or household_clock.today_for_household(engine, household_id)
    this_month_start = today.replace(day=1)
    window_start = add_months(this_month_start, -3)
    window_end = this_month_start - timedelta(days=1)

    take_home_money = monthly_income_total(
        engine, household_id, currency, today=today
    )
    take_home = Qualified(
        take_home_money.value.amount_minor, take_home_money.incomplete_sources
    )
    spending_total = repository.sum_spending(
        engine, household_id, window_start, window_end, currency
    )
    spending = spending_total.map(lambda value: round(value / 3))

    # Payroll deductions: pre-tax retirement + HSA across earners, monthlyised.
    profiles = repository.list_income_profiles(engine, household_id)
    payroll = sum(
        p.retirement_contribution_annual_minor + p.hsa_contribution_annual_minor
        for p in profiles
    ) // 12

    # Declared transfers only — a detected guess is not "what the household does".
    detection = savings_detection.qualified_detect_for_household(
        engine, household_id, today=today
    )
    transfers_value = sum(
        savings_detection.monthly_equivalent_minor(c)
        for c in detection.value
        if c.declared and c.currency == currency
    )
    transfers = (
        Qualified.complete(transfers_value) if detection.is_complete else None
    )

    # Residual: unspent take-home that didn't move to a named contribution.
    # Transfers are excluded from spending, so subtract them here to avoid
    # double-counting them as both a transfer and unspent cash.
    dependency_sources = union_sources(take_home, spending, detection)
    gross = residual = total_saved = None
    percent = None
    if not dependency_sources:
        gross_value = take_home.value + payroll
        residual_value = take_home.value - spending.value - transfers_value
        total_saved_value = payroll + take_home.value - spending.value
        gross = Qualified.complete(gross_value)
        residual = Qualified.complete(residual_value)
        total_saved = Qualified.complete(total_saved_value)
        percent = None if gross_value <= 0 else round(total_saved_value / gross_value * 100)

    return ObservedSavingsRate(
        percent=percent,
        gross_monthly=gross,
        take_home_monthly=take_home,
        spending_monthly=spending,
        transfers_monthly=transfers,
        transfer_detection=Qualified(None, detection.incomplete_sources),
        payroll_monthly_minor=payroll,
        residual_monthly=residual,
        total_saved_monthly=total_saved,
        has_payroll_profile=any(
            p.retirement_contribution_annual_minor or p.hsa_contribution_annual_minor
            for p in profiles
        ),
        has_declared_transfers=transfers_value > 0,
    )


def w2_baseline_monthly(engine: Engine, household_id: str, currency: str) -> Money | None:
    """Monthly gross implied by the W2 / compensation profiles, or None if the
    household declared none. This is a baseline reference shown next to actual
    income (which is net money-in) — deliberately NOT part of monthly_income_total.
    Gross = base + RSU + bonus, matching how the profile is declared."""
    # Lazy import: rsu_service imports add_months from this module.
    from family_cfo_api import rsu_service

    profiles = rsu_service.effective_income_profiles(engine, household_id)
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
    total, _excluded = _monthly_debt_minimums_with_exclusions(engine, household_id, currency)
    return total


def _monthly_debt_minimums_with_exclusions(
    engine: Engine, household_id: str, currency: str
) -> tuple[Money, list[ExcludedAccount]]:
    """Monthly minimum payments on loans, cards, and other liabilities that make a
    recurring claim on liquid cash — for the emergency-fund denominator (ADR 0039).

    Also returns the out-of-base liabilities that WOULD have contributed (#152
    review): the same loops that build the figure record what they skipped, so
    the disclosure is derived from the denominator's actual inputs rather than
    guessed from account types. A 401(k) loan is never a contributor, so it is
    never "excluded" either.

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
    balance_by_id = {
        b.account_id: b.balance_minor for b in repository.list_account_balances(engine, household_id)
    }
    total = Money.zero(currency)
    modeled_ids: set[str] = set(bill_covered)
    excluded: dict[str, ExcludedAccount] = {}
    for debt in repository.list_debts_with_terms(engine, household_id):
        if debt.minimum_payment_minor is None:
            continue
        modeled_ids.add(debt.account_id)
        if debt.account_id in bill_covered or debt.account_type in repository.RETIREMENT_LOAN_TYPES:
            continue
        if debt.currency != currency:
            excluded[debt.account_id] = ExcludedAccount(
                debt.account_id,
                debt.name,
                debt.account_type,
                Money(-debt.balance_owed_minor, debt.currency),
            )
            continue
        total += Money(debt.minimum_payment_minor, debt.currency)
    # Liabilities without a payoff balance (a lease, a card carried at its minimum)
    # never appear in list_debts_with_terms but still claim cash every month.
    for account in liability_accounts:
        if (
            account.minimum_payment_minor is None
            or account.id in modeled_ids
            or account.account_type in repository.RETIREMENT_LOAN_TYPES
        ):
            continue
        modeled_ids.add(account.id)
        if account.currency != currency:
            excluded[account.id] = ExcludedAccount(
                account.id,
                account.name,
                account.account_type,
                Money(balance_by_id.get(account.id, 0), account.currency),
            )
            continue
        total += Money(account.minimum_payment_minor, account.currency)
    return total, list(excluded.values())


def monthly_essential_expenses(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> Qualified[Money]:
    expenses, _excluded = monthly_essential_expenses_with_exclusions(
        engine, household_id, currency, today=today
    )
    return expenses


def monthly_essential_expenses_with_exclusions(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> tuple[Qualified[Money], list[ExcludedAccount]]:
    """The realistic monthly cash a household must cover if income stopped — the
    emergency-fund coverage denominator (ADR 0039) — and the out-of-base
    liabilities it left out (#152 review).

    ``= recurring bills + debt minimum payments + everyday spending above bills``

    Bill payments are categorized transactions, so they are ALREADY inside average
    spending; debt minimum payments are transfers, so they are NOT. To count every
    dollar once we take trailing-3-month average spending, strip the bill portion
    back out (``max(0, avg - bills)``), then add the explicit bills and the debt
    minimums that no bill already covers. Bills-only — the previous denominator —
    was absurdly optimistic: it ignored groceries, gas, and every loan/card payment,
    so a fund covered "months" of a household that in reality spends far more."""
    today = today or household_clock.today_for_household(engine, household_id)
    this_month_start = today.replace(day=1)
    # Last 3 complete calendar months, matching the savings-rate window (M44).
    window_start = add_months(this_month_start, -3)
    window_end = this_month_start - timedelta(days=1)

    bills = _monthly_bill_total(engine, household_id, currency)
    debt_minimums, excluded = _monthly_debt_minimums_with_exclusions(
        engine, household_id, currency
    )

    spending_3mo = repository.sum_spending(
        engine, household_id, window_start, window_end, currency
    )
    avg_spending_minor = max(0, round(spending_3mo.value / 3))
    # Average spending already contains the bill-categorized payments; keep only the
    # part above the recurring bills so housing/utilities aren't counted twice.
    spending_above_bills = Money(max(0, avg_spending_minor - bills.amount_minor), currency)

    return Qualified(
        bills + debt_minimums + spending_above_bills,
        spending_3mo.incomplete_sources,
    ), excluded


def _serialize_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    def serialize(value: Any) -> Any:
        if isinstance(value, Qualified):
            return {
                "value": serialize(value.value),
                "incomplete_count": value.incomplete_count,
            }
        if isinstance(value, Money):
            return value.to_dict()
        if isinstance(value, dict):
            return {key: serialize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [serialize(item) for item in value]
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
