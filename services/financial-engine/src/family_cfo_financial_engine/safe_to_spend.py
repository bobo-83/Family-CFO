from __future__ import annotations

from dataclasses import dataclass

from family_cfo_financial_engine.money import CurrencyMismatchError, Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult


@dataclass(frozen=True, slots=True)
class SafeToSpendInputs:
    """What is actually free to spend, once every claim on the cash is honoured.

    Money sitting in checking is not the same as money you can spend. Bills fall
    due and debts demand a minimum payment whether or not you bought a birthday
    present, so both are already spoken for. Subtracting only the emergency fund
    -- the old rule -- overstated what was available by exactly the amount the
    family owed in the coming weeks, which is the moment they most need the
    number to be right.
    """

    liquid_balance: Money
    """Checking + savings. Retirement and education funds are never in here."""

    emergency_fund_reserved: Money
    """Money the family explicitly earmarked for emergencies. Untouchable."""

    bills_due: Money
    """Bills falling due within `horizon_days`."""

    minimum_debt_payments: Money
    """Minimum payments owed on liability accounts within `horizon_days`."""

    horizon_days: int = 30

    credit_card_payments: Money | None = None
    """Full credit-card balances, when the household pays its cards in full each
    month — the whole balance is about to leave liquid cash, so it is committed,
    not just the minimum. Zero/None keeps the minimum-payment behaviour."""

    subscription_forecast: Money | None = None
    """Recurring subscription charges whose NEXT occurrence falls within
    `horizon_days` and has not yet been paid this cycle (ADR 0020). Reserved the
    'bill way' — only the upcoming in-window charge, never a monthly total — so a
    charge already deducted from liquid is never double-counted."""

    total_debt: Money | None = None
    """What the household owes across all liability accounts, as a positive amount.

    Not subtracted -- a balance is not due this month -- but reported, because
    "you have $6,765 to spend" alongside a silent $29,931 of credit-card debt is
    a true sentence that misleads. The family has to see both numbers together.
    """

    unmodeled_debt_count: int = 0
    """Liabilities with no recorded minimum payment: their claim is invisible."""

    unmodeled_debt_total: Money | None = None
    """What those unrecorded liabilities owe, so the warning can name a number."""


def _plain(money: Money) -> str:
    """A bare amount for warning text (the API formats display strings)."""
    return f"{money.amount_minor / 100:,.2f} {money.currency}"


def calculate_safe_to_spend(inputs: SafeToSpendInputs) -> CalculationResult:
    currency = inputs.liquid_balance.currency
    for other in (
        inputs.emergency_fund_reserved,
        inputs.bills_due,
        inputs.minimum_debt_payments,
    ):
        if other.currency != currency:
            raise CurrencyMismatchError(currency, other.currency)

    card_payments = inputs.credit_card_payments or Money.zero(currency)
    if card_payments.currency != currency:
        raise CurrencyMismatchError(currency, card_payments.currency)
    subscription_forecast = inputs.subscription_forecast or Money.zero(currency)
    if subscription_forecast.currency != currency:
        raise CurrencyMismatchError(currency, subscription_forecast.currency)
    committed = (
        inputs.emergency_fund_reserved
        + inputs.bills_due
        + inputs.minimum_debt_payments
        + card_payments
        + subscription_forecast
    )
    safe_to_spend = inputs.liquid_balance - committed

    warnings: list[str] = []

    if safe_to_spend.is_negative():
        warnings.append(
            "Committed obligations exceed liquid balances: after the emergency fund, "
            "bills due and debt payments, there is no discretionary money — spending "
            "here means missing an obligation."
        )

    if inputs.unmodeled_debt_count > 0:
        owed = ""
        if inputs.unmodeled_debt_total is not None and not inputs.unmodeled_debt_total.is_zero():
            owed = f" (owing {_plain(inputs.unmodeled_debt_total)})"
        warnings.append(
            f"{inputs.unmodeled_debt_count} liability account(s){owed} have no minimum payment "
            "recorded, so nothing was subtracted for them: the amount already committed to "
            "debt is UNDERSTATED and the true safe-to-spend figure is LOWER than shown. "
            "Record each account's minimum payment to fix this."
        )

    if inputs.total_debt is not None and not inputs.total_debt.is_zero():
        warnings.append(
            f"The household owes {_plain(inputs.total_debt)} across its liability accounts. "
            "Spendable cash must be reported alongside that debt, never on its own."
        )

    if inputs.emergency_fund_reserved.is_zero():
        warnings.append(
            "No emergency fund is designated, so nothing is held back for emergencies. "
            "This figure protects no reserve."
        )

    if inputs.bills_due.is_zero():
        warnings.append(
            "No bills fall due in this window. If the household has bills that are not "
            "recorded, this figure is overstated."
        )

    return CalculationResult(
        calculation_type="safe_to_spend",
        version=CALCULATION_ENGINE_VERSION,
        inputs={
            "currency": currency,
            "horizon_days": inputs.horizon_days,
            "unmodeled_debt_count": inputs.unmodeled_debt_count,
        },
        assumptions=[
            "Only checking and savings balances are spendable; retirement and education "
            "funds are excluded.",
            "Designated emergency-fund money is not available to spend.",
            f"Bills falling due within {inputs.horizon_days} days are already committed.",
            f"Minimum debt payments due within {inputs.horizon_days} days are already committed.",
            "Income expected during the window is NOT counted — this is what is safe to "
            "spend from money already in the bank.",
        ],
        outputs={
            "liquid_balance": inputs.liquid_balance,
            "emergency_fund_reserved": inputs.emergency_fund_reserved,
            "bills_due": inputs.bills_due,
            "minimum_debt_payments": inputs.minimum_debt_payments,
            "credit_card_payments": card_payments,
            "subscription_forecast": subscription_forecast,
            "committed_total": committed,
            "safe_to_spend": safe_to_spend,
            "total_debt": inputs.total_debt or Money.zero(currency),
        },
        warnings=warnings,
    )
