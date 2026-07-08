import logging

from fastapi import APIRouter, Depends, HTTPException
from family_cfo_financial_engine import Money as EngineMoney
from family_cfo_financial_engine import RetirementInput
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, repository
from family_cfo_api.ai_runtime_selection import select_explanation_adapter
from family_cfo_api.deps import get_current_session, get_engine
from family_cfo_api.explanation import PurchaseExplanationContext, format_money
from family_cfo_api.schemas import (
    ErrorResponse,
    Impact,
    PurchaseAdvisorRequest,
    Recommendation,
    RetirementScenarioRequest,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Advisor"])
logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.4
_BASE_CONFIDENCE = 0.9
_CONFIDENCE_PENALTY_PER_WARNING = 0.15


def _debt_impact(outlook: finance_service.DebtOutlook) -> Impact | None:
    if outlook.modeled_count == 0 and outlook.unmodeled_count == 0:
        return None  # no debt at all

    parts: list[str] = []
    if outlook.modeled_count > 0:
        if outlook.total_interest_remaining is not None and outlook.longest_months is not None:
            parts.append(
                f"Across {outlook.modeled_count} debt(s) with terms, remaining interest is "
                f"{format_money(outlook.total_interest_remaining)} and the longest payoff is "
                f"{outlook.longest_months} months at current payments."
            )
        else:
            parts.append(
                f"{outlook.modeled_count} debt(s) with terms could not be projected — "
                "the payment does not cover accruing interest."
            )
        parts.append(
            "Paying cash for this purchase reduces funds available for extra debt payments."
        )
    if outlook.unmodeled_count > 0:
        parts.append(
            f"{outlook.unmodeled_count} debt(s) have no interest/payment terms set and were not modeled."
        )

    return Impact(area="debt", summary=" ".join(parts))


def _build_impacts(
    outputs: dict, warnings: list[str], debt_outlook: finance_service.DebtOutlook
) -> list[Impact]:
    price = outputs["price"]
    net_worth_delta = outputs["net_worth_after"] - outputs["net_worth_before"]

    impacts = [
        Impact(
            area="net_worth",
            summary=(
                f"Net worth would move from {format_money(outputs['net_worth_before'])} to "
                f"{format_money(outputs['net_worth_after'])}."
            ),
            amount=MoneySchema(**net_worth_delta.to_dict()),
        ),
    ]

    ef_before = outputs["emergency_fund_months_before"]
    ef_after = outputs["emergency_fund_months_after"]
    if ef_before is not None and ef_after is not None:
        impacts.append(
            Impact(
                area="emergency_fund",
                summary=f"Emergency fund coverage would move from {ef_before:.1f} to {ef_after:.1f} months.",
            )
        )

    if outputs["discretionary_months_consumed"] is not None:
        impacts.append(
            Impact(
                area="cash_flow",
                summary=(
                    f"This purchase equals about {outputs['discretionary_months_consumed']:.1f} "
                    "months of discretionary cash flow."
                ),
                amount=MoneySchema(**price.to_dict()),
            )
        )

    if outputs["top_goal_impact_percent"] is not None:
        impacts.append(
            Impact(
                area="savings_goal",
                summary=(
                    f"This purchase is about {outputs['top_goal_impact_percent']:.1f}% of what's "
                    "remaining on your top-priority goal."
                ),
                amount=MoneySchema(**price.to_dict()),
            )
        )

    debt_impact = _debt_impact(debt_outlook)
    if debt_impact is not None:
        impacts.append(debt_impact)

    return impacts


def _build_confidence(warning_count: int) -> float:
    confidence = _BASE_CONFIDENCE - _CONFIDENCE_PENALTY_PER_WARNING * warning_count
    return round(max(_MIN_CONFIDENCE, min(confidence, _BASE_CONFIDENCE)), 2)


def _build_tradeoffs_and_alternatives(exceeds_liquid_balance: bool) -> tuple[list[str], list[str]]:
    tradeoffs = ["Paying in cash avoids interest but reduces your liquid safety net."]
    alternatives = ["Delay the purchase until emergency fund coverage recovers."]

    if exceeds_liquid_balance:
        tradeoffs.append("The purchase price exceeds your currently available liquid balance.")
        alternatives.append(
            "Finance a portion of the purchase to preserve liquidity, if acceptable terms are available."
        )

    return tradeoffs, alternatives


@router.post(
    "/advisor/purchase",
    operation_id="analyzePurchase",
    response_model=Recommendation,
    responses={
        400: {"description": "Invalid purchase request", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
    },
    summary="Analyze the financial impact of a potential purchase",
)
async def analyze_purchase(
    payload: PurchaseAdvisorRequest,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> Recommendation:
    if payload.price.amount_minor <= 0:
        raise HTTPException(status_code=400, detail="Purchase price must be positive")

    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")

    currency = household.base_currency
    if payload.price.currency != currency:
        raise HTTPException(
            status_code=400,
            detail=f"Purchase price currency must be {currency}",
        )

    price = EngineMoney(payload.price.amount_minor, payload.price.currency)

    scenario_id = repository.create_scenario(
        engine,
        household_id=session.household_id,
        created_by_user_id=session.user_id,
        name=f"Purchase: {payload.item}",
        description=payload.description,
        input_json=payload.model_dump(mode="json"),
    )

    result, calculation_id = finance_service.compute_purchase_impact(
        engine, session.household_id, currency, price
    )
    debt_outlook = finance_service.compute_debt_outlook(engine, session.household_id, currency)

    impacts = _build_impacts(result.outputs, result.warnings, debt_outlook)
    confidence = _build_confidence(len(result.warnings))
    exceeds_liquid_balance = any("exceeds available liquid balance" in w for w in result.warnings)
    tradeoffs, alternatives = _build_tradeoffs_and_alternatives(exceeds_liquid_balance)
    calculation_refs = [f"financial_calculations:{calculation_id}", *debt_outlook.calculation_refs]

    explanation_context = PurchaseExplanationContext(
        item=payload.item,
        price=price,
        net_worth_after=result.outputs["net_worth_after"],
        emergency_fund_months_before=result.outputs["emergency_fund_months_before"],
        emergency_fund_months_after=result.outputs["emergency_fund_months_after"],
        discretionary_months_consumed=result.outputs["discretionary_months_consumed"],
        warnings=result.warnings,
    )
    explanation_adapter, runtime_client = select_explanation_adapter(engine, session.household_id)
    try:
        explanation = explanation_adapter.explain_purchase(explanation_context)
    finally:
        if runtime_client is not None:
            runtime_client.close()

    recommendation_id = repository.create_recommendation(
        engine,
        household_id=session.household_id,
        scenario_id=scenario_id,
        answer=explanation.text,
        assumptions=result.assumptions,
        impacts=[impact.model_dump(mode="json") for impact in impacts],
        tradeoffs=tradeoffs,
        alternatives=alternatives,
        confidence=confidence,
        calculation_refs=calculation_refs,
        warnings=result.warnings,
        explanation_source=explanation.source,
        model_version=explanation.model_version,
        prompt_version=explanation.prompt_version,
    )

    logger.info(
        "purchase advisor recommendation created household_id=%s calculation_id=%s "
        "recommendation_id=%s explanation_source=%s",
        session.household_id,
        calculation_id,
        recommendation_id,
        explanation.source,
    )

    return Recommendation(
        id=recommendation_id,
        answer=explanation.text,
        assumptions=result.assumptions,
        impacts=impacts,
        tradeoffs=tradeoffs,
        alternatives=alternatives,
        confidence=confidence,
        calculation_refs=calculation_refs,
        warnings=result.warnings,
    )


@router.post(
    "/advisor/retirement",
    operation_id="analyzeRetirement",
    response_model=Recommendation,
    responses={
        400: {"description": "Invalid retirement scenario", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
    },
    summary="Project retirement savings for a scenario",
)
async def analyze_retirement(
    payload: RetirementScenarioRequest,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> Recommendation:
    if payload.retirement_age <= payload.current_age:
        raise HTTPException(
            status_code=400, detail="retirement_age must be greater than current_age"
        )

    currency = payload.current_savings.currency
    if payload.monthly_contribution.currency != currency or (
        payload.annual_expenses is not None and payload.annual_expenses.currency != currency
    ):
        raise HTTPException(status_code=400, detail="all amounts must use the same currency")

    inputs = RetirementInput(
        current_age=payload.current_age,
        retirement_age=payload.retirement_age,
        current_savings=EngineMoney(payload.current_savings.amount_minor, currency),
        monthly_contribution=EngineMoney(payload.monthly_contribution.amount_minor, currency),
        annual_return_rate=payload.annual_return_rate,
        annual_expenses=(
            EngineMoney(payload.annual_expenses.amount_minor, currency)
            if payload.annual_expenses is not None
            else None
        ),
    )
    result, calculation_id = finance_service.compute_retirement_projection(
        engine, session.household_id, inputs
    )

    projected = result.outputs["projected_balance"]
    years = result.outputs["months_to_retirement"] // 12
    coverage = result.outputs["years_of_expenses_covered"]

    answer_parts = [
        f"Contributing {format_money(inputs.monthly_contribution)} per month at a "
        f"{payload.annual_return_rate * 100:.1f}% annual return, your savings would grow to about "
        f"{format_money(projected)} by age {payload.retirement_age} (in {years} years)."
    ]
    if coverage is not None and inputs.annual_expenses is not None:
        answer_parts.append(
            f"That covers about {coverage:.1f} years of spending at "
            f"{format_money(inputs.annual_expenses)} per year in retirement."
        )
    if result.warnings:
        answer_parts.append("Note: " + " ".join(result.warnings))
    answer = " ".join(answer_parts)

    impacts = [
        Impact(
            area="retirement",
            summary=f"Projected balance at retirement: {format_money(projected)}.",
            amount=MoneySchema(**projected.to_dict()),
        )
    ]
    if coverage is not None:
        impacts.append(
            Impact(
                area="retirement",
                summary=f"Approximately {coverage:.1f} years of expenses covered.",
            )
        )

    tradeoffs = ["Projections assume a constant return and contribution; real markets vary."]
    alternatives = [
        "Increase monthly contributions or delay retirement to grow the projected balance."
    ]
    confidence = _build_confidence(len(result.warnings))
    calculation_refs = [f"financial_calculations:{calculation_id}"]

    scenario_id = repository.create_scenario(
        engine,
        household_id=session.household_id,
        created_by_user_id=session.user_id,
        name="Retirement projection",
        description=None,
        input_json=payload.model_dump(mode="json"),
    )
    recommendation_id = repository.create_recommendation(
        engine,
        household_id=session.household_id,
        scenario_id=scenario_id,
        answer=answer,
        assumptions=result.assumptions,
        impacts=[impact.model_dump(mode="json") for impact in impacts],
        tradeoffs=tradeoffs,
        alternatives=alternatives,
        confidence=confidence,
        calculation_refs=calculation_refs,
        warnings=result.warnings,
        explanation_source="deterministic_stub",
    )

    logger.info(
        "retirement projection created household_id=%s recommendation_id=%s calculation_id=%s",
        session.household_id,
        recommendation_id,
        calculation_id,
    )

    return Recommendation(
        id=recommendation_id,
        answer=answer,
        assumptions=result.assumptions,
        impacts=impacts,
        tradeoffs=tradeoffs,
        alternatives=alternatives,
        confidence=confidence,
        calculation_refs=calculation_refs,
        warnings=result.warnings,
    )
