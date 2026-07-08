from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from family_cfo_financial_engine import (
    CalculationResult,
    CategorySpend,
    GoalInput,
    Money,
    RecurringAmount,
    calculate_budget_summary,
    calculate_cash_flow,
    calculate_goal_progress,
)
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.ai_runtime_selection import select_explanation_adapter
from family_cfo_api.explanation import ExplanationAdapter, ReportExplanationContext, format_money

REPORT_CALCULATION_VERSION = "1.0.0"
REPORT_TYPES = ("weekly", "monthly")

# A new spending category above this threshold is "unusual" rather than noise. Fixed and
# documented, not a calibrated/learned value -- there is no model to calibrate against yet.
_UNUSUAL_SPENDING_THRESHOLD_MINOR = 5_000
# A category spend increase past this ratio (20%) versus the prior period is a "risk".
_RISK_INCREASE_RATIO = 1.2


@dataclass(frozen=True, slots=True)
class ReportPeriod:
    start: date
    end_exclusive: date


def compute_report_period(report_type: str, reference_date: date) -> ReportPeriod:
    """The period a report covers, ending the day before `reference_date` (never including today)."""
    if report_type == "weekly":
        end_exclusive = reference_date
        start = end_exclusive - timedelta(days=7)
    elif report_type == "monthly":
        end_exclusive = reference_date.replace(day=1)
        if end_exclusive.month == 1:
            start = end_exclusive.replace(year=end_exclusive.year - 1, month=12)
        else:
            start = end_exclusive.replace(month=end_exclusive.month - 1)
    else:
        raise ValueError(f"unsupported report_type: {report_type!r}")

    return ReportPeriod(start=start, end_exclusive=end_exclusive)


def _previous_period(period: ReportPeriod) -> ReportPeriod:
    length = period.end_exclusive - period.start
    return ReportPeriod(start=period.start - length, end_exclusive=period.start)


def _scale_for_report_type(amount: Money, report_type: str) -> Money:
    """Scale a `calculate_cash_flow` monthly figure down to the report's period.

    A week is treated as a fixed 7/30 fraction of a month -- an approximation
    recorded as a report assumption, the same way the financial engine's own
    12-months-per-year normalization is documented as an assumption.
    """
    if report_type == "monthly":
        return amount
    if report_type == "weekly":
        return amount.scale(7, 30)
    raise ValueError(f"unsupported report_type: {report_type!r}")


def _category_totals(
    transactions: list[repository.TransactionRecord], currency: str
) -> dict[str, Money]:
    totals: dict[str, Money] = {}
    for txn in transactions:
        if txn.amount_minor >= 0:
            continue  # only expenses count toward category spend
        category = txn.category or "Uncategorized"
        totals[category] = totals.get(category, Money.zero(currency)) + Money(
            -txn.amount_minor, currency
        )
    return totals


def _serialize(value: Any) -> Any:
    if isinstance(value, Money):
        return value.to_dict()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def _persist_calculation(engine: Engine, household_id: str, result: CalculationResult) -> str:
    return repository.record_calculation(
        engine,
        household_id=household_id,
        calculation_type=result.calculation_type,
        version=result.version,
        inputs=result.inputs,
        assumptions=result.assumptions,
        warnings=result.warnings,
        outputs=_serialize(result.outputs),
    )


@dataclass(frozen=True, slots=True)
class ReportContent:
    period: ReportPeriod
    net_cash_flow: Money
    wins: list[str]
    risks: list[str]
    unusual_spending: list[str]
    recommended_actions: list[str]
    goal_progress: list[dict[str, Any]]
    calculation_refs: list[str]


def _wins_risks_unusual(
    current_totals: dict[str, Money],
    previous_totals: dict[str, Money],
    remaining: Money,
    currency: str,
) -> tuple[list[str], list[str], list[str], list[str]]:
    wins: list[str] = []
    risks: list[str] = []
    unusual_spending: list[str] = []
    recommended_actions: list[str] = []

    if remaining.is_negative():
        risks.append(
            f"Spending exceeded income and bills by {format_money(-remaining)} this period."
        )
        recommended_actions.append(
            "Review discretionary spending or upcoming bills to close the shortfall."
        )
    else:
        wins.append(f"You stayed within budget with {format_money(remaining)} remaining.")

    for category, current_amount in current_totals.items():
        previous_amount = previous_totals.get(category)
        if previous_amount is None:
            if current_amount.amount_minor >= _UNUSUAL_SPENDING_THRESHOLD_MINOR:
                unusual_spending.append(
                    f"New spending in {category}: {format_money(current_amount)}."
                )
            continue

        if previous_amount.amount_minor <= 0:
            continue

        if current_amount.ratio(previous_amount) >= _RISK_INCREASE_RATIO:
            risks.append(
                f"{category} spending rose from {format_money(previous_amount)} to {format_money(current_amount)}."
            )
            recommended_actions.append(
                f"Review your {category} spending, which increased from last period."
            )

    return wins, risks, unusual_spending, recommended_actions


def _build_report_content(
    engine: Engine, household_id: str, currency: str, report_type: str, reference_date: date
) -> ReportContent:
    period = compute_report_period(report_type, reference_date)
    previous = _previous_period(period)

    current_transactions = repository.list_transactions_in_range(
        engine, household_id, period.start, period.end_exclusive
    )
    previous_transactions = repository.list_transactions_in_range(
        engine, household_id, previous.start, previous.end_exclusive
    )

    current_totals = _category_totals(current_transactions, currency)
    previous_totals = _category_totals(previous_transactions, currency)

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
    cash_flow_calculation_id = _persist_calculation(engine, household_id, cash_flow_result)

    period_income = _scale_for_report_type(cash_flow_result.outputs["monthly_income"], report_type)
    period_bills = _scale_for_report_type(cash_flow_result.outputs["monthly_bills"], report_type)
    category_spend = [
        CategorySpend(category=cat, amount=amt) for cat, amt in current_totals.items()
    ]

    budget_result = calculate_budget_summary(period_income, period_bills, category_spend, currency)
    budget_calculation_id = _persist_calculation(engine, household_id, budget_result)

    net_cash_flow = budget_result.outputs["remaining"]
    wins, risks, unusual_spending, recommended_actions = _wins_risks_unusual(
        current_totals, previous_totals, net_cash_flow, currency
    )

    goal_progress: list[dict[str, Any]] = []
    for goal in repository.list_goals(engine, household_id):
        goal_input = GoalInput(
            goal_id=goal.id,
            name=goal.name,
            target=Money(goal.target_minor, goal.currency),
            current=Money(goal.current_minor, goal.currency),
        )
        goal_result = calculate_goal_progress(goal_input)
        goal_calculation_id = _persist_calculation(engine, household_id, goal_result)
        goal_progress.append(
            {
                "goal_id": goal.id,
                "name": goal.name,
                "percent_complete": goal_result.outputs["percent_complete"],
                "months_to_completion": goal_result.outputs["months_to_completion"],
                "calculation_ref": f"financial_calculations:{goal_calculation_id}",
            }
        )
        if (
            goal_result.outputs["percent_complete"] is not None
            and goal_result.outputs["percent_complete"] >= 100
        ):
            wins.append(f"Goal '{goal.name}' is fully funded.")

    calculation_refs = [
        f"financial_calculations:{cash_flow_calculation_id}",
        f"financial_calculations:{budget_calculation_id}",
    ]

    return ReportContent(
        period=period,
        net_cash_flow=net_cash_flow,
        wins=wins,
        risks=risks,
        unusual_spending=unusual_spending,
        recommended_actions=recommended_actions,
        goal_progress=goal_progress,
        calculation_refs=calculation_refs,
    )


def generate_report(
    engine: Engine,
    household_id: str,
    report_type: str,
    explanation_adapter: ExplanationAdapter,
    reference_date: date | None = None,
) -> repository.ReportRecord:
    household = repository.get_household(engine, household_id)
    if household is None:
        raise ValueError(f"household {household_id} not found")

    content = _build_report_content(
        engine, household_id, household.base_currency, report_type, reference_date or date.today()
    )
    period_end_inclusive = content.period.end_exclusive - timedelta(days=1)

    explanation_context = ReportExplanationContext(
        report_type=report_type,
        period_start=content.period.start.isoformat(),
        period_end=period_end_inclusive.isoformat(),
        net_cash_flow=content.net_cash_flow,
        wins=content.wins,
        risks=content.risks,
        unusual_spending=content.unusual_spending,
        recommended_actions=content.recommended_actions,
    )
    explanation = explanation_adapter.explain_report(explanation_context)

    summary = {
        "wins": content.wins,
        "risks": content.risks,
        "unusual_spending": content.unusual_spending,
        "recommended_actions": content.recommended_actions,
        "goal_progress": content.goal_progress,
        "net_cash_flow": content.net_cash_flow.to_dict(),
        "calculation_refs": content.calculation_refs,
    }

    return repository.upsert_report(
        engine,
        household_id=household_id,
        report_type=report_type,
        period_start=content.period.start,
        period_end=period_end_inclusive,
        summary=summary,
        explanation_text=explanation.text,
        explanation_source=explanation.source,
        calculation_version=REPORT_CALCULATION_VERSION,
        model_version=explanation.model_version,
        prompt_version=explanation.prompt_version,
    )


def run_scheduled_reports_once(
    engine: Engine, report_type: str, reference_date: date | None = None
) -> int:
    """Generate `report_type` for every household whose current period isn't generated yet.

    Called directly by tests (synchronous, deterministic) and polled daily by
    the worker, following the exact pattern `run_pending_imports_once`
    established in M7. Skipping already-generated periods means a missed
    poll self-heals on the next poll without needing true cron semantics or
    re-calling the AI runtime for a period that's already covered.
    """
    reference = reference_date or date.today()
    period = compute_report_period(report_type, reference)

    generated = 0
    for household_id in repository.list_households(engine):
        if (
            repository.get_report_by_period(engine, household_id, report_type, period.start)
            is not None
        ):
            continue

        explanation_adapter, runtime_client = select_explanation_adapter(engine, household_id)
        try:
            generate_report(engine, household_id, report_type, explanation_adapter, reference)
            generated += 1
        finally:
            if runtime_client is not None:
                runtime_client.close()

    return generated
