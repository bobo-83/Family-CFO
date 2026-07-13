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

    unmodeled_debt_count: int = 0
    """Liabilities with no recorded minimum payment: their claim is invisible."""


def calculate_safe_to_spend(inputs: SafeToSpendInputs) -> CalculationResult:
    currency = inputs.liquid_balance.currency
    for other in (
        inputs.emergency_fund_reserved,
        inputs.bills_due,
        inputs.minimum_debt_payments,
    ):
        if other.currency != currency:
            raise CurrencyMismatchError(currency, other.currency)

    committed = inputs.emergency_fund_reserved + inputs.bills_due + inputs.minimum_debt_payments
    safe_to_spend = inputs.liquid_balance - committed

    warnings: list[str] = []

    if safe_to_spend.is_negative():
        warnings.append(
            "Committed obligations exceed liquid balances: after the emergency fund, "
            "bills due and debt payments, there is no discretionary money — spending "
            "here means missing an obligation."
        )

    if inputs.unmodeled_debt_count > 0:
        warnings.append(
            f"{inputs.unmodeled_debt_count} liability account(s) have no minimum payment "
            "recorded, so the amount already committed to debt is UNDERSTATED and the "
            "true safe-to-spend figure is lower than shown."
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
            "committed_total": committed,
            "safe_to_spend": safe_to_spend,
        },
        warnings=warnings,
    )
