"""App-side tool library for the agentic advisor (M16, ADR 0009).

The model orchestrates the deterministic financial engine by calling these
tools. The trust boundary is here: every tool validates its arguments before
touching the engine, read tools are scoped to the caller's household (the model
never supplies an entity id), and every returned figure comes from an engine
calculation -- never from the model. Bad arguments and missing facts are
returned as structured error payloads (never raised) so the loop can feed them
back for the model to correct itself or ask the user.
"""

from __future__ import annotations

import json
from typing import Any

from family_cfo_ai_orchestrator import ToolCallingResult, ToolSpec, extract_numbers
from family_cfo_financial_engine import (
    CalculationResult,
    DebtInput,
    FutureValueInput,
    Money,
    RetirementInput,
)
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service
from family_cfo_api.explanation import format_money
from family_cfo_ai_orchestrator.tool_calling import ToolExecutor

# The model must ground every number in a tool result and must never invent a
# figure. When a required input is missing it asks the user rather than guess.
TOOL_SYSTEM_PROMPT = (
    "You are a household financial assistant for a single family. Answer the "
    "user's question using ONLY the provided tools for any financial figure. "
    "Never state a number, amount, or projection you did not obtain from a tool "
    "result. Amounts are in minor currency units (e.g. cents); pass them as "
    "integers and quote the human-readable 'display' string from tool results "
    "back to the user. If a tool reports \"error\": \"missing_input\", ask the "
    "user to supply that fact instead of guessing. If a tool reports "
    "\"error\": \"invalid_arguments\", correct the arguments and try again. Keep "
    "the final answer to a few plain-language sentences."
)


def _money_out(money: Money) -> dict[str, Any]:
    return {
        "amount_minor": money.amount_minor,
        "currency": money.currency,
        "display": format_money(money),
    }


def _serialize(value: Any) -> Any:
    if isinstance(value, Money):
        return _money_out(value)
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


def _result_payload(result: CalculationResult, calculation_id: str) -> dict[str, Any]:
    return {
        "outputs": {key: _serialize(value) for key, value in result.outputs.items()},
        "assumptions": list(result.assumptions),
        "warnings": list(result.warnings),
        "calculation_ref": f"financial_calculations:{calculation_id}",
    }


# --- argument validation helpers -------------------------------------------
# Each returns (value, error_dict). On failure value is None and the caller
# should return the error payload immediately.


def _missing(field: str) -> dict[str, Any]:
    return {"error": "missing_input", "missing": field}


def _invalid(detail: str) -> dict[str, Any]:
    return {"error": "invalid_arguments", "detail": detail}


def _int_arg(
    args: dict[str, Any], field: str, *, minimum: int | None = None
) -> tuple[int | None, dict[str, Any] | None]:
    if field not in args or args[field] is None:
        return None, _missing(field)
    value = args[field]
    if isinstance(value, bool) or not isinstance(value, int):
        return None, _invalid(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        return None, _invalid(f"{field} must be >= {minimum}")
    return value, None


def _rate_arg(
    args: dict[str, Any], field: str
) -> tuple[float | None, dict[str, Any] | None]:
    if field not in args or args[field] is None:
        return None, _missing(field)
    value = args[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, _invalid(f"{field} must be a number")
    if value < 0 or value > 1:
        return None, _invalid(f"{field} must be a decimal fraction between 0 and 1")
    return float(value), None


def _currency_arg(
    args: dict[str, Any], household_currency: str
) -> tuple[str | None, dict[str, Any] | None]:
    # This household is single-currency; the model may omit currency (it
    # defaults) but may not name a different one.
    supplied = args.get("currency")
    if supplied is None:
        return household_currency, None
    if not isinstance(supplied, str) or supplied.upper() != household_currency:
        return None, _invalid(
            f"this household operates in {household_currency}; other currencies are not supported"
        )
    return household_currency, None


# --- tool handlers ----------------------------------------------------------


def _get_net_worth(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    result, calc_id = finance_service.compute_net_worth_with_ref(engine, household_id, currency)
    return _result_payload(result, calc_id)


def _get_emergency_fund(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    result, calc_id = finance_service.compute_emergency_fund_with_ref(
        engine, household_id, currency
    )
    return _result_payload(result, calc_id)


def _get_debt_outlook(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    outlook = finance_service.compute_debt_outlook(engine, household_id, currency)
    return {
        "modeled_debts": outlook.modeled_count,
        "unmodeled_debts": outlook.unmodeled_count,
        "total_interest_remaining": (
            _money_out(outlook.total_interest_remaining)
            if outlook.total_interest_remaining is not None
            else None
        ),
        "longest_payoff_months": outlook.longest_months,
        "warnings": list(outlook.warnings),
    }


def _project_purchase_impact(
    engine: Engine, household_id: str, currency: str, args: dict[str, Any]
):
    resolved_currency, error = _currency_arg(args, currency)
    if error:
        return error
    price_minor, error = _int_arg(args, "price_minor", minimum=0)
    if error:
        return error
    result, calc_id = finance_service.compute_purchase_impact(
        engine, household_id, resolved_currency, Money(price_minor, resolved_currency)
    )
    return _result_payload(result, calc_id)


def _future_value(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    resolved_currency, error = _currency_arg(args, currency)
    if error:
        return error
    present_value_minor, error = _int_arg(args, "present_value_minor", minimum=0)
    if error:
        return error
    rate, error = _rate_arg(args, "annual_return_rate")
    if error:
        return error
    years, error = _int_arg(args, "years", minimum=0)
    if error:
        return error
    result, calc_id = finance_service.compute_future_value(
        engine,
        household_id,
        FutureValueInput(
            present_value=Money(present_value_minor, resolved_currency),
            annual_return_rate=rate,
            years=years,
        ),
    )
    return _result_payload(result, calc_id)


def _project_retirement(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    resolved_currency, error = _currency_arg(args, currency)
    if error:
        return error
    current_age, error = _int_arg(args, "current_age", minimum=0)
    if error:
        return error
    retirement_age, error = _int_arg(args, "retirement_age", minimum=0)
    if error:
        return error
    if retirement_age <= current_age:
        return _invalid("retirement_age must be greater than current_age")
    current_savings_minor, error = _int_arg(args, "current_savings_minor", minimum=0)
    if error:
        return error
    monthly_contribution_minor, error = _int_arg(args, "monthly_contribution_minor", minimum=0)
    if error:
        return error
    rate, error = _rate_arg(args, "annual_return_rate")
    if error:
        return error
    # annual_expenses is optional -- only supplied when the user asks about
    # expense coverage / "can we retire" style questions.
    annual_expenses = None
    if args.get("annual_expenses_minor") is not None:
        expenses_minor, error = _int_arg(args, "annual_expenses_minor", minimum=0)
        if error:
            return error
        annual_expenses = Money(expenses_minor, resolved_currency)

    result, calc_id = finance_service.compute_retirement_projection(
        engine,
        household_id,
        RetirementInput(
            current_age=current_age,
            retirement_age=retirement_age,
            current_savings=Money(current_savings_minor, resolved_currency),
            monthly_contribution=Money(monthly_contribution_minor, resolved_currency),
            annual_return_rate=rate,
            annual_expenses=annual_expenses,
        ),
    )
    return _result_payload(result, calc_id)


def _debt_payoff(engine: Engine, household_id: str, currency: str, args: dict[str, Any]):
    resolved_currency, error = _currency_arg(args, currency)
    if error:
        return error
    balance_minor, error = _int_arg(args, "balance_minor", minimum=0)
    if error:
        return error
    rate, error = _rate_arg(args, "annual_interest_rate")
    if error:
        return error
    minimum_payment_minor, error = _int_arg(args, "minimum_payment_minor", minimum=0)
    if error:
        return error
    extra = None
    if args.get("extra_monthly_payment_minor") is not None:
        extra_minor, error = _int_arg(args, "extra_monthly_payment_minor", minimum=0)
        if error:
            return error
        extra = Money(extra_minor, resolved_currency)

    result, calc_id = finance_service.compute_debt_payoff(
        engine,
        household_id,
        DebtInput(
            debt_id="hypothetical",
            name="hypothetical debt",
            balance=Money(balance_minor, resolved_currency),
            annual_interest_rate=rate,
            minimum_payment=Money(minimum_payment_minor, resolved_currency),
            extra_monthly_payment=extra,
        ),
    )
    return _result_payload(result, calc_id)


_HANDLERS = {
    "get_net_worth": _get_net_worth,
    "get_emergency_fund": _get_emergency_fund,
    "get_debt_outlook": _get_debt_outlook,
    "project_purchase_impact": _project_purchase_impact,
    "future_value": _future_value,
    "project_retirement": _project_retirement,
    "debt_payoff": _debt_payoff,
}

_MONEY_FIELD = {"type": "integer", "description": "amount in minor currency units (e.g. cents)"}
_CURRENCY_FIELD = {
    "type": "string",
    "description": "ISO currency code; defaults to the household base currency",
}
_RATE_FIELD = {"type": "number", "description": "annual rate as a decimal fraction, e.g. 0.06 for 6%"}


def build_tools() -> list[ToolSpec]:
    """The JSON-schema descriptors advertised to the model."""
    return [
        ToolSpec(
            name="get_net_worth",
            description="Current household net worth, total assets, and total liabilities.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="get_emergency_fund",
            description="Months of essential expenses the household's liquid savings would cover.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="get_debt_outlook",
            description=(
                "Payoff outlook across the household's liability accounts that carry terms: "
                "remaining interest and the longest payoff horizon."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="project_purchase_impact",
            description="How a one-off purchase of the given price affects net worth and cash buffers.",
            parameters={
                "type": "object",
                "properties": {"price_minor": _MONEY_FIELD, "currency": _CURRENCY_FIELD},
                "required": ["price_minor"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="future_value",
            description=(
                "Future value of a lump sum after N years of annual compounding -- use for "
                "opportunity-cost questions (what an amount could grow to if invested)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "present_value_minor": _MONEY_FIELD,
                    "annual_return_rate": _RATE_FIELD,
                    "years": {"type": "integer", "description": "number of whole years"},
                    "currency": _CURRENCY_FIELD,
                },
                "required": ["present_value_minor", "annual_return_rate", "years"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="project_retirement",
            description=(
                "Project retirement savings from current age to retirement age given monthly "
                "contributions and a return rate. Supply annual_expenses_minor to also estimate "
                "how many years of expenses the balance would cover."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "current_age": {"type": "integer"},
                    "retirement_age": {"type": "integer"},
                    "current_savings_minor": _MONEY_FIELD,
                    "monthly_contribution_minor": _MONEY_FIELD,
                    "annual_return_rate": _RATE_FIELD,
                    "annual_expenses_minor": {
                        **_MONEY_FIELD,
                        "description": "optional: annual retirement spending, for coverage estimate",
                    },
                    "currency": _CURRENCY_FIELD,
                },
                "required": [
                    "current_age",
                    "retirement_age",
                    "current_savings_minor",
                    "monthly_contribution_minor",
                    "annual_return_rate",
                ],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="debt_payoff",
            description=(
                "Months to pay off a debt and total interest paid, for a hypothetical balance, "
                "interest rate, and monthly payment the user describes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "balance_minor": _MONEY_FIELD,
                    "annual_interest_rate": _RATE_FIELD,
                    "minimum_payment_minor": _MONEY_FIELD,
                    "extra_monthly_payment_minor": {
                        **_MONEY_FIELD,
                        "description": "optional: extra paid on top of the minimum each month",
                    },
                    "currency": _CURRENCY_FIELD,
                },
                "required": ["balance_minor", "annual_interest_rate", "minimum_payment_minor"],
                "additionalProperties": False,
            },
        ),
    ]


def build_executor(engine: Engine, household_id: str, currency: str) -> ToolExecutor:
    """A household-scoped dispatcher the tool-calling loop invokes for each tool call."""

    def execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = _HANDLERS.get(name)
        if handler is None:
            return {"error": "unknown_tool", "name": name}
        return handler(engine, household_id, currency, args)

    return execute


def grounded_values(result: ToolCallingResult) -> set[str]:
    """Numbers the model was allowed to use: everything in the tool call trace.

    Both tool inputs (echoing a user-supplied figure is legitimate) and tool
    outputs (the engine's computed figures) count as grounded. Any number in the
    final answer outside this set is an invented figure and fails the guardrail.
    """
    known: set[str] = set()
    for record in result.tool_calls:
        known |= extract_numbers(json.dumps(record.arguments))
        known |= extract_numbers(json.dumps(record.result))
    return known
