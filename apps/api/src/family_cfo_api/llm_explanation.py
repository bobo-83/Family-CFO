from __future__ import annotations

import logging

from family_cfo_ai_orchestrator import (
    PURCHASE_EXPLANATION_PROMPT_VERSION,
    PurchaseFacts,
    RuntimeAdapter,
    RuntimeUnavailableError,
    build_purchase_explanation_prompt,
    known_values_from_facts,
    validate_recommendation,
)

from family_cfo_api.explanation import (
    DeterministicExplanationAdapter,
    ExplanationResult,
    PurchaseExplanationContext,
    format_money,
)

logger = logging.getLogger(__name__)


class LlmExplanationAdapter:
    """Calls a configured ``RuntimeAdapter`` and validates its output against guardrails.

    Falls back to the deterministic stub on adapter error or a guardrail
    violation, so the advisor route never surfaces an unvalidated numeric
    claim and never hard-fails when the runtime is unreachable (ADR 0003).
    """

    def __init__(self, runtime_adapter: RuntimeAdapter, model_version: str) -> None:
        self._runtime_adapter = runtime_adapter
        self._model_version = model_version
        self._fallback = DeterministicExplanationAdapter()

    def explain_purchase(self, context: PurchaseExplanationContext) -> ExplanationResult:
        facts = PurchaseFacts(
            item=context.item,
            price_display=format_money(context.price),
            net_worth_after_display=format_money(context.net_worth_after),
            emergency_fund_months_before=context.emergency_fund_months_before,
            emergency_fund_months_after=context.emergency_fund_months_after,
            discretionary_months_consumed=context.discretionary_months_consumed,
            warnings=context.warnings,
        )
        messages = build_purchase_explanation_prompt(facts)

        try:
            completion = self._runtime_adapter.complete(messages)
        except RuntimeUnavailableError:
            logger.warning("ai runtime unavailable; falling back to deterministic explanation")
            return self._fallback.explain_purchase(context)

        known_values = known_values_from_facts(facts)
        guardrail = validate_recommendation(completion.text, known_values)
        if not guardrail.passed:
            logger.warning(
                "ai runtime response failed guardrail validation; falling back to deterministic explanation"
            )
            return self._fallback.explain_purchase(context)

        return ExplanationResult(
            text=completion.text,
            source="llm",
            model_version=completion.model or self._model_version,
            prompt_version=PURCHASE_EXPLANATION_PROMPT_VERSION,
        )
