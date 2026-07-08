from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, repository
from family_cfo_api.deps import get_current_session, get_engine
from family_cfo_api.explanation import format_money
from family_cfo_api.schemas import ChatRequest, ChatResponse, ErrorResponse, Impact, Recommendation
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Chat"])
logger = logging.getLogger(__name__)

_TITLE_MAX_LENGTH = 80


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


@router.post(
    "/chat/messages",
    operation_id="createChatMessage",
    response_model=ChatResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="Send a message to the financial advisor",
)
async def create_chat_message(
    payload: ChatRequest,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> ChatResponse:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")

    net_worth_result, net_worth_calculation_id = finance_service.compute_net_worth_with_ref(
        engine, household.id, household.base_currency
    )
    emergency_result, emergency_calculation_id = finance_service.compute_emergency_fund_with_ref(
        engine, household.id, household.base_currency
    )

    net_worth = net_worth_result.outputs["net_worth"]
    emergency_months = emergency_result.outputs["emergency_fund_months"]
    emergency_text = (
        "Emergency fund coverage could not be calculated from current bill data."
        if emergency_months is None
        else f"Emergency fund coverage is {emergency_months:.1f} months."
    )
    answer = (
        f"Current household snapshot: net worth is {format_money(net_worth)}. "
        f"{emergency_text} M6 chat is limited to deterministic household context; "
        "purchase questions should use the purchase advisor workflow."
    )

    impacts = [
        Impact(
            area="net_worth",
            summary=f"Current net worth is {format_money(net_worth)}.",
            amount=MoneySchema(**net_worth.to_dict()),
        ),
        Impact(area="emergency_fund", summary=emergency_text),
    ]
    calculation_refs = [
        f"financial_calculations:{net_worth_calculation_id}",
        f"financial_calculations:{emergency_calculation_id}",
    ]
    warnings = _dedupe([*net_worth_result.warnings, *emergency_result.warnings])
    recommendation_id = repository.create_recommendation(
        engine,
        household_id=household.id,
        scenario_id=None,
        answer=answer,
        assumptions=_dedupe([*net_worth_result.assumptions, *emergency_result.assumptions]),
        impacts=[impact.model_dump(mode="json") for impact in impacts],
        tradeoffs=["The chat endpoint answers from current stored household context only."],
        alternatives=["Use the purchase advisor for item-specific affordability analysis."],
        confidence=0.75 if warnings else 0.85,
        calculation_refs=calculation_refs,
        warnings=warnings,
        explanation_source="deterministic_stub",
    )

    # M10: persist the thread. A missing/unknown conversation_id starts a new
    # conversation titled from the first message; an existing one is appended to.
    conversation = None
    if payload.conversation_id is not None:
        conversation = repository.get_conversation(engine, household.id, payload.conversation_id)
    if conversation is None:
        title = payload.message.strip()[:_TITLE_MAX_LENGTH] or "Conversation"
        conversation = repository.create_conversation(
            engine, household_id=household.id, created_by_user_id=session.user_id, title=title
        )
    repository.append_conversation_turn(
        engine,
        conversation_id=conversation.id,
        user_content=payload.message,
        assistant_content=answer,
        recommendation_id=recommendation_id,
    )
    conversation_id = conversation.id

    logger.info(
        "chat recommendation created household_id=%s recommendation_id=%s conversation_id=%s",
        household.id,
        recommendation_id,
        conversation_id,
    )

    return ChatResponse(
        conversation_id=conversation_id,
        recommendation=Recommendation(
            id=recommendation_id,
            answer=answer,
            assumptions=_dedupe([*net_worth_result.assumptions, *emergency_result.assumptions]),
            impacts=impacts,
            tradeoffs=["The chat endpoint answers from current stored household context only."],
            alternatives=["Use the purchase advisor for item-specific affordability analysis."],
            confidence=0.75 if warnings else 0.85,
            calculation_refs=calculation_refs,
            warnings=warnings,
        ),
    )
