from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_ai_orchestrator import (
    RuntimeMessage,
    RuntimeUnavailableError,
    describe_image,
    extract_numbers,
    run_tool_calling_loop,
    validate_recommendation,
)

from family_cfo_api import ai_tools, finance_service, repository
from family_cfo_api.ai_runtime_selection import select_tool_runtime, select_vision_describer
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine
from family_cfo_api.explanation import format_money
from family_cfo_api.schemas import ChatRequest, ChatResponse, ErrorResponse, Impact, Recommendation
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Chat"])
logger = logging.getLogger(__name__)

_TITLE_MAX_LENGTH = 80

_NO_VISION_WARNING = (
    "An attached photo could not be analyzed because no vision-capable AI model "
    "is configured; the answer does not consider the image."
)


@dataclass(frozen=True, slots=True)
class _ImageAnalysis:
    """Outcome of the describe step (ADR 0011). The image itself is never stored."""

    description: str | None
    warning: str | None
    source: str  # "main" | "describer" | "none"


def _validate_image(payload: ChatRequest, settings: Settings) -> str | None:
    """Return the image data URL, or None if no image; raise 4xx on bad input."""
    if payload.image_base64 is None:
        return None
    if payload.image_media_type is None:
        raise HTTPException(status_code=422, detail="image_media_type is required with an image")
    try:
        raw = base64.b64decode(payload.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="image_base64 is not valid base64") from exc
    if not raw:
        raise HTTPException(status_code=422, detail="attached image is empty")
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="attached image exceeds the maximum size")
    return f"data:{payload.image_media_type};base64,{payload.image_base64}"


def _analyze_image(
    engine: Engine, household_id: str, settings: Settings, image_data_url: str, message: str
) -> _ImageAnalysis:
    """Describe the photo per ADR 0011; degrade to a warning, never an error."""
    describer, source = select_vision_describer(engine, household_id, settings)
    if describer is None:
        return _ImageAnalysis(description=None, warning=_NO_VISION_WARNING, source="none")
    try:
        description = describe_image(describer, image_data_url, user_context=message)
    except RuntimeUnavailableError:
        logger.warning("vision describer unavailable; continuing without image analysis")
        return _ImageAnalysis(
            description=None,
            warning="The photo could not be analyzed right now (vision model unavailable).",
            source=source,
        )
    finally:
        describer.close()
    return _ImageAnalysis(description=description, warning=None, source=source)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


@dataclass(slots=True)
class _Answer:
    """The fields needed to persist a recommendation and build the response."""

    answer: str
    assumptions: list[str] = field(default_factory=list)
    impacts: list[Impact] = field(default_factory=list)
    tradeoffs: list[str] = field(default_factory=list)
    alternatives: list[str] = field(default_factory=list)
    confidence: float = 0.8
    calculation_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explanation_source: str = "deterministic_stub"


def _deterministic_answer(engine: Engine, household: repository.HouseholdRecord) -> _Answer:
    """The always-available fallback: a snapshot of net worth and emergency-fund coverage."""
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
        f"{emergency_text} This answer is a deterministic snapshot; enable a local "
        "AI runtime for open-ended questions."
    )
    warnings = _dedupe([*net_worth_result.warnings, *emergency_result.warnings])
    return _Answer(
        answer=answer,
        assumptions=_dedupe([*net_worth_result.assumptions, *emergency_result.assumptions]),
        impacts=[
            Impact(
                area="net_worth",
                summary=f"Current net worth is {format_money(net_worth)}.",
                amount=MoneySchema(**net_worth.to_dict()),
            ),
            Impact(area="emergency_fund", summary=emergency_text),
        ],
        tradeoffs=["The chat endpoint answers from current stored household context only."],
        alternatives=["Use the purchase advisor for item-specific affordability analysis."],
        confidence=0.75 if warnings else 0.85,
        calculation_refs=[
            f"financial_calculations:{net_worth_calculation_id}",
            f"financial_calculations:{emergency_calculation_id}",
        ],
        warnings=warnings,
        explanation_source="deterministic_stub",
    )


def _try_agentic_answer(
    engine: Engine,
    household: repository.HouseholdRecord,
    message: str,
    *,
    image_description: str | None = None,
    settings: Settings | None = None,
) -> _Answer | None:
    """Attempt an agentic tool-calling answer; return None to signal a deterministic fallback.

    Falls back (returns None) when no runtime is configured, the runtime is
    unavailable, the loop does not converge within its iteration cap, or the
    final answer contains a number not grounded in a tool result (ADR 0009).
    """
    runtime = select_tool_runtime(engine, household.id)
    if runtime is None:
        return None

    tools = ai_tools.build_tools(settings)
    executor = ai_tools.build_executor(engine, household.id, household.base_currency, settings)
    # ADR 0011: the photo enters the loop as its text description only; the
    # description's numbers are grounded below since they trace to the image.
    user_content = message
    if image_description:
        user_content = f"{message}\n\n[Attached photo, as described by the vision model: {image_description}]"
    messages = [
        RuntimeMessage(role="system", content=ai_tools.TOOL_SYSTEM_PROMPT),
        RuntimeMessage(role="user", content=user_content),
    ]
    try:
        result = run_tool_calling_loop(runtime, messages, tools, executor)
    except RuntimeUnavailableError:
        logger.warning("agentic chat runtime unavailable; falling back to deterministic snapshot")
        return None
    finally:
        runtime.close()

    if not result.completed or result.answer is None:
        logger.warning("agentic chat loop did not converge; falling back to deterministic snapshot")
        return None

    known_values = ai_tools.grounded_values(result)
    # The user's own figures are legitimate to echo back ("your $2,000") — they
    # are context, not fabrication. Derived arithmetic must still come from a
    # tool: a model-computed product is in neither set and fails closed.
    known_values |= extract_numbers(message)
    if image_description:
        known_values |= extract_numbers(image_description)
    guardrail = validate_recommendation(result.answer, known_values)
    if not guardrail.passed:
        logger.warning(
            "agentic chat answer had ungrounded numbers %s; falling back", guardrail.violations
        )
        return None

    refs: list[str] = []
    assumptions: list[str] = []
    warnings: list[str] = []
    for record in result.tool_calls:
        ref = record.result.get("calculation_ref")
        if isinstance(ref, str):
            refs.append(ref)
        assumptions.extend(record.result.get("assumptions", []) or [])
        warnings.extend(record.result.get("warnings", []) or [])

    return _Answer(
        answer=result.answer,
        assumptions=_dedupe(assumptions),
        impacts=[],
        tradeoffs=["Produced by the local AI model using deterministic financial tools."],
        alternatives=[],
        confidence=0.8,
        calculation_refs=_dedupe(refs),
        warnings=_dedupe(warnings),
        explanation_source="agentic_tool_calling",
    )


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
    settings: Settings = Depends(get_app_settings),
) -> ChatResponse:
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")

    # ADR 0011: an attached photo is described (never stored) before the loop.
    analysis = None
    image_data_url = _validate_image(payload, settings)
    if image_data_url is not None:
        analysis = _analyze_image(
            engine, household.id, settings, image_data_url, payload.message
        )

    answer = _try_agentic_answer(
        engine,
        household,
        payload.message,
        image_description=analysis.description if analysis else None,
        settings=settings,
    ) or _deterministic_answer(engine, household)

    if analysis and analysis.warning:
        answer.warnings = _dedupe([*answer.warnings, analysis.warning])

    recommendation_id = repository.create_recommendation(
        engine,
        household_id=household.id,
        scenario_id=None,
        answer=answer.answer,
        assumptions=answer.assumptions,
        impacts=[impact.model_dump(mode="json") for impact in answer.impacts],
        tradeoffs=answer.tradeoffs,
        alternatives=answer.alternatives,
        confidence=answer.confidence,
        calculation_refs=answer.calculation_refs,
        warnings=answer.warnings,
        explanation_source=answer.explanation_source,
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
    # The stored turn records the photo's description, never the image itself.
    stored_user_content = payload.message
    if analysis is not None:
        note = analysis.description or "photo attached (not analyzed)"
        stored_user_content = f"{payload.message}\n\n[Photo: {note}]"
    repository.append_conversation_turn(
        engine,
        conversation_id=conversation.id,
        user_content=stored_user_content,
        assistant_content=answer.answer,
        recommendation_id=recommendation_id,
    )

    logger.info(
        "chat recommendation created household_id=%s recommendation_id=%s conversation_id=%s source=%s",
        household.id,
        recommendation_id,
        conversation.id,
        answer.explanation_source,
    )

    return ChatResponse(
        conversation_id=conversation.id,
        recommendation=Recommendation(
            id=recommendation_id,
            answer=answer.answer,
            assumptions=answer.assumptions,
            impacts=answer.impacts,
            tradeoffs=answer.tradeoffs,
            alternatives=answer.alternatives,
            confidence=answer.confidence,
            calculation_refs=answer.calculation_refs,
            warnings=answer.warnings,
        ),
    )
