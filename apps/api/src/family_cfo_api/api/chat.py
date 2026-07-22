from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.engine import Engine

from family_cfo_ai_orchestrator import (
    RuntimeMessage,
    RuntimeUnavailableError,
    ToolCallRecord,
    describe_image,
    extract_numbers,
    run_tool_calling_loop,
    validate_recommendation,
)

from family_cfo_api import ai_memory, ai_tools, finance_service, repository
from family_cfo_api.ai_runtime_selection import (
    resolve_ai_config,
    select_tool_runtime,
    select_vision_describer,
)
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine
from family_cfo_api.explanation import format_money
from family_cfo_api.schemas import (
    AdvisorFeedbackRequest,
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    ErrorResponse,
    Impact,
    Recommendation,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Chat"])
logger = logging.getLogger(__name__)

_TITLE_MAX_LENGTH = 80

# Conversational memory (M30): how much of the stored thread the model sees.
_HISTORY_MAX_MESSAGES = 8
_HISTORY_MESSAGE_MAX_CHARS = 1500
# The loop's 500-token default truncated multi-step plans mid-sentence (user
# report 2026-07-20). A final answer needs room; the model still stops at its
# natural end, so this only lifts the ceiling, it doesn't pad short answers.
# Reasoning models (Qwen3.6) spend tokens thinking BEFORE the visible answer:
# 1200 was fully consumed by thinking on open-ended questions, yielding an
# empty answer (user report 2026-07-21) -- the budget must cover both.
_ANSWER_MAX_TOKENS = 2400
# The library default (6) was too few once the toolset grew to ~16 and the
# grounding asks the advisor to consult several tools for a plan (debt outlook,
# bills, spending, find_savings…): a thorough answer exhausted the rounds and
# fell back to the deterministic snapshot. Give it room to gather AND conclude.
_ANSWER_MAX_ITERATIONS = 12

# ADR 0061: streamed progress events. An event is a small JSON dict pushed to
# the client while the loop works; the ANSWER is only ever sent after the
# grounding guardrail passed — streaming changes what the user sees while
# waiting, never what they see verified.
ChatEventSink = Callable[[dict], None]

# Friendly progress lines for the tools the advisor calls most; anything not
# listed is humanized from its snake_case name.
_TOOL_PROGRESS_LABELS = {
    "when_can_i_retire": "Solving for your retirement age",
    "project_retirement": "Projecting retirement savings",
    "safe_to_spend": "Checking safe-to-spend",
    "list_bills": "Reviewing your bills",
    "debt_outlook": "Assessing debts",
    "find_savings": "Looking for savings",
    "net_worth": "Adding up net worth",
    "emergency_fund": "Checking the emergency fund",
}


def _tool_progress_label(name: str) -> str:
    return _TOOL_PROGRESS_LABELS.get(name, f"Checking {name.replace('_', ' ')}")

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
    described_by: str | None = None  # model id that read the photo


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
    if payload.image_media_type == "application/pdf":
        # M84a: the vision model reads pixels — rasterize page 1 (M77 path).
        from family_cfo_api.api.income_analysis import pdf_page_pngs

        png = pdf_page_pngs(raw, max_pages=1)[0]
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return f"data:{payload.image_media_type};base64,{payload.image_base64}"


def _data_file_preview(payload: ChatRequest, settings: Settings) -> str | None:
    """M85: a bounded grounded summary of an attached data file, or None."""
    if payload.data_file_base64 is None:
        return None
    try:
        raw = base64.b64decode(payload.data_file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="data_file_base64 is not valid base64") from exc
    if not raw:
        raise HTTPException(status_code=422, detail="attached data file is empty")
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="attached data file exceeds the maximum size")
    from family_cfo_api.chat_attachments import build_data_file_preview

    return build_data_file_preview(payload.data_file_name or "attachment", raw)


def _analyze_image(
    engine: Engine, household_id: str, settings: Settings, image_data_url: str, message: str
) -> _ImageAnalysis:
    """Describe the photo per ADR 0011; degrade to a warning, never an error."""
    describer, source = select_vision_describer(engine, household_id, settings)
    if describer is None:
        return _ImageAnalysis(description=None, warning=_NO_VISION_WARNING, source="none")
    described_by = (
        settings.ai_vision_model
        if source == "describer"
        else resolve_ai_config(engine, household_id, settings).model
    )
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
    return _ImageAnalysis(
        description=description, warning=None, source=source, described_by=described_by
    )


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
    # The model id that produced the answer; None for deterministic answers.
    answered_by: str | None = None


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
    data_file_preview: str | None = None,
    settings: Settings | None = None,
    history: list[tuple[str, str]] | None = None,
    memories: list[str] | None = None,
    conversation_summary: str | None = None,
    household_context: str | None = None,
    on_event: ChatEventSink | None = None,
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
    if on_event is not None:
        inner_executor = executor

        def executor(name: str, arguments: dict):  # type: ignore[no-redef]
            on_event({"type": "progress", "stage": "tool", "tool": name, "detail": _tool_progress_label(name)})
            return inner_executor(name, arguments)
    # ADR 0011: the photo enters the loop as its text description only; the
    # description's numbers are grounded below since they trace to the image.
    user_content = message
    if image_description:
        user_content = f"{message}\n\n[Attached photo, as described by the vision model: {image_description}]"
    if data_file_preview:
        # M85: the file summary is the user's own data — grounded context.
        user_content = f"{user_content}\n\n[Attached data file summary:\n{data_file_preview}\n]"
    # M30: prior turns from this conversation give the model memory. Bounded
    # to the most recent messages, each truncated, to protect the context.
    history_messages = [
        RuntimeMessage(role=role, content=content[:_HISTORY_MESSAGE_MAX_CHARS])
        for role, content in (history or [])[-_HISTORY_MAX_MESSAGES:]
    ]
    # M57 (ADR 0016): durable facts learned across ALL conversations, plus the
    # rolling summary of this thread's turns older than the history window.
    # ALL system content is merged into ONE system message: newer chat templates
    # (e.g. Qwen3.6) reject a system message anywhere but the first position
    # ("System message must be at the beginning" -> HTTP 400).
    system_sections: list[str] = [ai_tools.build_system_prompt(settings)]
    if household_context:
        system_sections.append(household_context)
    if memories:
        facts = "\n".join(f"- {value}" for value in memories[:ai_memory.MAX_INJECTED_MEMORIES])
        system_sections.append(
            "Known household facts, remembered from previous conversations "
            f"(each individually deletable by the family):\n{facts}"
        )
    if conversation_summary:
        system_sections.append(
            f"Earlier in this conversation (summary): {conversation_summary}"
        )
    messages = [
        RuntimeMessage(role="system", content="\n\n".join(system_sections)),
        *history_messages,
        RuntimeMessage(role="user", content=user_content),
    ]
    # The user's own figures are legitimate to echo back ("your $2,000") — they
    # are context, not fabrication. Numbers in the included history are
    # grounded too: prior assistant answers passed the guardrail when they
    # were produced (M30). Remembered facts and the stored summary are context
    # the same way (M57).
    context_values = extract_numbers(message)
    for _role, content in (history or [])[-_HISTORY_MAX_MESSAGES:]:
        context_values |= extract_numbers(content)
    if image_description:
        context_values |= extract_numbers(image_description)
    if data_file_preview:
        context_values |= extract_numbers(data_file_preview)
    for value in memories or []:
        context_values |= extract_numbers(value)
    if conversation_summary:
        context_values |= extract_numbers(conversation_summary)

    tool_call_records: list[ToolCallRecord] = []
    try:
        result = run_tool_calling_loop(
            runtime,
            messages,
            tools,
            executor,
            max_tokens=_ANSWER_MAX_TOKENS,
            max_iterations=_ANSWER_MAX_ITERATIONS,
        )
        if not result.completed or not result.answer:
            logger.warning(
                "agentic chat loop did not converge; falling back to deterministic snapshot"
            )
            return None
        tool_call_records.extend(result.tool_calls)
        known_values = context_values | ai_tools.grounded_values(result)
        guardrail = validate_recommendation(result.answer, known_values)
        if not guardrail.passed:
            # M56: one corrective retry before failing closed — told which
            # figures were the problem, the model can usually restate with
            # tool-derived numbers or call a tool to compute them.
            logger.warning(
                "agentic chat answer had ungrounded numbers %s; retrying once",
                guardrail.violations,
            )
            if on_event is not None:
                on_event({"type": "progress", "stage": "revising", "detail": "Double-checking the figures"})
            retry_messages = [
                *messages,
                RuntimeMessage(role="assistant", content=result.answer),
                RuntimeMessage(
                    role="user",
                    content=(
                        "Your answer included figures that do not appear in any tool result: "
                        f"{', '.join(guardrail.violations)}. Restate it using only figures "
                        "returned by the tools — call a tool if you need to compute something."
                    ),
                ),
            ]
            retry = run_tool_calling_loop(
                runtime,
                retry_messages,
                tools,
                executor,
                max_tokens=_ANSWER_MAX_TOKENS,
                max_iterations=_ANSWER_MAX_ITERATIONS,
            )
            if not retry.completed or not retry.answer:
                logger.warning(
                    "agentic chat retry did not converge; falling back to deterministic snapshot"
                )
                return None
            # The first round's tool trace stays grounded: the retry may
            # restate those figures without re-calling the tools.
            tool_call_records.extend(retry.tool_calls)
            known_values |= ai_tools.grounded_values(retry)
            guardrail = validate_recommendation(retry.answer, known_values)
            if not guardrail.passed:
                logger.warning(
                    "agentic chat retry still had ungrounded numbers %s; falling back",
                    guardrail.violations,
                )
                return None
            result = retry
    except RuntimeUnavailableError:
        logger.warning("agentic chat runtime unavailable; falling back to deterministic snapshot")
        return None
    finally:
        runtime.close()

    refs: list[str] = []
    assumptions: list[str] = []
    warnings: list[str] = []
    for record in tool_call_records:
        ref = record.result.get("calculation_ref")
        if isinstance(ref, str):
            refs.append(ref)
        assumptions.extend(record.result.get("assumptions", []) or [])
        warnings.extend(record.result.get("warnings", []) or [])

    config = resolve_ai_config(engine, household.id, settings)
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
        answered_by=config.model or None,
    )


def _chat_turn(
    payload: ChatRequest,
    session: repository.SessionContext,
    engine: Engine,
    settings: Settings,
    *,
    schedule: Callable[..., None],
    on_event: ChatEventSink | None = None,
) -> ChatResponse:
    """One full advisor turn: analyze attachments, run the grounded loop,
    persist, and build the response. Shared by the plain and streaming
    endpoints; `schedule` defers post-response work (memory extraction) and
    `on_event` receives progress events while the loop runs (ADR 0061)."""
    household = repository.get_household(engine, session.household_id)
    if household is None:
        raise HTTPException(status_code=404, detail="Household not found")

    # M30: resolve the conversation up front so its prior turns become model
    # memory (an unknown id still starts a fresh thread, as in M10).
    conversation = None
    history: list[tuple[str, str]] = []
    if payload.conversation_id is not None:
        conversation = repository.get_conversation(engine, household.id, payload.conversation_id, session.user_id)
        if conversation is not None:
            history = [
                (m.role, m.content)
                for m in repository.list_conversation_messages(engine, conversation.id)
            ]

    # ADR 0011: an attached photo is described (never stored) before the loop.
    analysis = None
    image_data_url = _validate_image(payload, settings)
    if image_data_url is not None:
        if on_event is not None:
            on_event({"type": "progress", "stage": "photo", "detail": "Reading the attached photo"})
        analysis = _analyze_image(
            engine, household.id, settings, image_data_url, payload.message
        )

    # M85: an attached data file becomes a bounded, grounded prompt summary.
    data_file_preview = _data_file_preview(payload, settings)

    # M57: facts remembered across all conversations + this thread's summary.
    memories = [m.value for m in repository.list_household_memories(engine, household.id)]

    me = repository.get_member(engine, household.id, session.user_id)
    first_name = me.display_name.split()[0] if me and me.display_name else None
    earliest_month, latest_month = repository.transaction_month_range(engine, household.id)
    household_context = ai_tools.build_household_context(
        currency=household.base_currency,
        first_name=first_name,
        member_count=len(repository.list_members(engine, household.id)),
        earliest_month=earliest_month,
        latest_month=latest_month,
    )

    if on_event is not None:
        on_event({"type": "progress", "stage": "thinking", "detail": "Thinking with your numbers"})
    answer = _try_agentic_answer(
        engine,
        household,
        payload.message,
        image_description=analysis.description if analysis else None,
        data_file_preview=data_file_preview,
        settings=settings,
        history=history,
        memories=memories,
        conversation_summary=conversation.summary if conversation else None,
        household_context=household_context,
        on_event=on_event,
    ) or _deterministic_answer(engine, household)

    if analysis and analysis.warning:
        answer.warnings = _dedupe([*answer.warnings, analysis.warning])
    photo_described_by = analysis.described_by if analysis and analysis.description else None
    if photo_described_by:
        # Persisted in assumptions_json so attribution survives in the audit trail.
        answer.assumptions = _dedupe(
            [*answer.assumptions, f"Attached photo was read by the vision model {photo_described_by}."]
        )

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
        model_version=answer.answered_by,
    )

    # M10: persist the thread. A missing/unknown conversation_id starts a new
    # conversation titled from the first message; an existing one is appended to.
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
    if data_file_preview is not None:
        stored_user_content = f"{stored_user_content}\n\n[Data file: {payload.data_file_name or 'attachment'}]"
    repository.append_conversation_turn(
        engine,
        conversation_id=conversation.id,
        user_content=stored_user_content,
        assistant_content=answer.answer,
        recommendation_id=recommendation_id,
    )

    # M57 (ADR 0016): after the response is sent, extract durable facts from
    # this message and refresh the thread summary. Best-effort; never raises.
    schedule(
        ai_memory.remember_exchange,
        engine,
        household.id,
        conversation.id,
        stored_user_content,
        settings,
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
            answered_by=answer.answered_by,
            photo_described_by=photo_described_by,
        ),
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
    background_tasks: BackgroundTasks,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> ChatResponse:
    return _chat_turn(
        payload, session, engine, settings, schedule=background_tasks.add_task
    )


@router.post(
    "/chat/messages/stream",
    operation_id="createChatMessageStream",
    responses={
        200: {
            "description": (
                "Server-sent events: `progress` events while the advisor works "
                "(stage/tool/detail), then exactly one `answer` event carrying the "
                "full ChatResponse once the grounding guardrail passed, or one "
                "`error` event. Comment lines keep the socket alive."
            ),
            "content": {"text/event-stream": {"schema": ChatStreamEvent.model_json_schema()}},
        },
        401: {"description": "Unauthorized", "model": ErrorResponse},
    },
    summary="Send a message to the financial advisor (streamed progress)",
)
async def create_chat_message_stream(
    payload: ChatRequest,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> StreamingResponse:
    """ADR 0061: the answer itself is NEVER streamed token-by-token — it is
    sent whole, after the guardrail validated it. Streaming exists so the
    socket carries bytes while the model thinks (weak-WiFi connections drop
    idle sockets — nginx 499s) and so the user sees live progress."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    def emit(event: dict | None) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def schedule(func: Callable, /, *args) -> None:
        # Post-response work must not hold the stream open; run it after the
        # answer event, detached, mirroring BackgroundTasks semantics.
        loop.call_soon_threadsafe(
            lambda: loop.run_in_executor(None, lambda: func(*args))
        )

    def run_turn() -> None:
        try:
            response = _chat_turn(
                payload, session, engine, settings, schedule=schedule, on_event=emit
            )
            emit({"type": "answer", "response": response.model_dump(mode="json", by_alias=True)})
        except HTTPException as exc:
            emit({"type": "error", "message": str(exc.detail)})
        except Exception:
            logger.exception("streamed chat turn failed")
            emit({"type": "error", "message": "The advisor hit an unexpected error."})
        finally:
            emit(None)

    worker = loop.run_in_executor(None, run_turn)

    async def sse():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                except TimeoutError:
                    yield ": ping\n\n"  # keepalive — the point of streaming
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # A disconnected client must NOT cancel the turn: the worker thread
            # keeps running, finishes, and saves the answer so the clients'
            # SavedAnswerRecovery can find it. (No await here — the generator
            # may be closing due to a disconnect, where awaiting is illegal.)
            _ = worker

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/chat/feedback",
    operation_id="submitAdvisorFeedback",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Recommendation not found", "model": ErrorResponse},
    },
    summary="Rate an advisor answer (👍/👎); the study job learns from it",
)
async def submit_advisor_feedback(
    payload: AdvisorFeedbackRequest,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> Response:
    # Scope the recommendation to the caller's household — a member can't rate
    # (or probe) another household's answers.
    if not repository.recommendation_belongs_to_household(
        engine, session.household_id, payload.recommendation_id
    ):
        raise HTTPException(status_code=404, detail="Recommendation not found")
    repository.upsert_advisor_feedback(
        engine,
        session.household_id,
        payload.recommendation_id,
        session.user_id,
        payload.rating,
        payload.note,
    )
    return Response(status_code=204)
