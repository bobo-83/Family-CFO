from __future__ import annotations

from typing import Any

from family_cfo_financial_engine import (
    AccountBalance,
    CalculationResult,
    Money,
    RecurringAmount,
    calculate_emergency_fund_months,
    calculate_net_worth,
)
from sqlalchemy.engine import Engine

from family_cfo_api import repository

LIQUID_ACCOUNT_TYPES = frozenset({"checking", "savings"})


def compute_net_worth(engine: Engine, household_id: str, currency: str) -> CalculationResult:
    balances = repository.list_account_balances(engine, household_id)
    engine_balances = [
        AccountBalance(b.account_id, b.account_type, Money(b.balance_minor, b.currency)) for b in balances
    ]

    result = calculate_net_worth(engine_balances, currency)
    _persist(engine, household_id, result)
    return result


def compute_emergency_fund(engine: Engine, household_id: str, currency: str) -> CalculationResult:
    balances = repository.list_account_balances(engine, household_id)
    liquid_balance = Money.zero(currency)
    for balance in balances:
        if balance.account_type in LIQUID_ACCOUNT_TYPES:
            liquid_balance += Money(balance.balance_minor, balance.currency)

    monthly_bills = _monthly_bill_total(engine, household_id, currency)

    result = calculate_emergency_fund_months(liquid_balance, monthly_bills)
    _persist(engine, household_id, result)
    return result


def _monthly_bill_total(engine: Engine, household_id: str, currency: str) -> Money:
    bills = repository.list_bills(engine, household_id)
    total = Money.zero(currency)
    for bill in bills:
        recurring = RecurringAmount(bill.name, Money(bill.amount_minor, bill.currency), bill.frequency)
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


def _persist(engine: Engine, household_id: str, result: CalculationResult) -> None:
    repository.record_calculation(
        engine,
        household_id=household_id,
        calculation_type=result.calculation_type,
        version=result.version,
        inputs=result.inputs,
        assumptions=result.assumptions,
        warnings=result.warnings,
        outputs=_serialize_outputs(result.outputs),
    )
