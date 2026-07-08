import logging

from fastapi import APIRouter, Depends, HTTPException
from family_cfo_financial_engine import Money as EngineMoney
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, repository
from family_cfo_api.ai_runtime_selection import select_explanation_adapter
from family_cfo_api.deps import get_current_session, get_engine
from family_cfo_api.explanation import PurchaseExplanationContext, format_money
from family_cfo_api.schemas import ErrorResponse, Impact, PurchaseAdvisorRequest, Recommendation
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Advisor"])
logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.4
_BASE_CONFIDENCE = 0.9
_CONFIDENCE_PENALTY_PER_WARNING = 0.15


def _build_impacts(outputs: dict, warnings: list[str]) -> list[Impact]:
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

    if any("debt payoff impact" in warning for warning in warnings):
        impacts.append(
            Impact(
                area="debt",
                summary="The household carries debt, but payoff impact cannot be modeled without interest rate and payment data.",
            )
        )

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

    impacts = _build_impacts(result.outputs, result.warnings)
    confidence = _build_confidence(len(result.warnings))
    exceeds_liquid_balance = any("exceeds available liquid balance" in w for w in result.warnings)
    tradeoffs, alternatives = _build_tradeoffs_and_alternatives(exceeds_liquid_balance)
    calculation_refs = [f"financial_calculations:{calculation_id}"]

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
