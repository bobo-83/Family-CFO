from __future__ import annotations

from dataclasses import dataclass, field

from family_cfo_ai_orchestrator.runtime import RuntimeMessage

PURCHASE_EXPLANATION_PROMPT_VERSION = "purchase-advisor-v1"

_SYSTEM_PROMPT = (
    "You are a financial explanation assistant for a self-hosted household finance app. "
    "Only reference the numeric facts listed below. Never invent account balances, interest "
    "rates, or figures that are not present in the facts. Respond in 2-4 plain sentences."
)


@dataclass(frozen=True, slots=True)
class PurchaseFacts:
    item: str
    price_display: str
    net_worth_after_display: str
    emergency_fund_months_before: float | None = None
    emergency_fund_months_after: float | None = None
    discretionary_months_consumed: float | None = None
    warnings: list[str] = field(default_factory=list)


def purchase_fact_lines(facts: PurchaseFacts) -> list[str]:
    """The exact fact strings sent to the model.

    Guardrail validation reuses this so "known" numbers can never drift from
    what was actually sent in the prompt.
    """
    fact_lines = [
        f"Item: {facts.item}",
        f"Price: {facts.price_display}",
        f"Net worth after purchase: {facts.net_worth_after_display}",
    ]

    if facts.emergency_fund_months_before is not None and facts.emergency_fund_months_after is not None:
        fact_lines.append(
            f"Emergency fund coverage: {facts.emergency_fund_months_before:.1f} months before, "
            f"{facts.emergency_fund_months_after:.1f} months after"
        )

    if facts.discretionary_months_consumed is not None:
        fact_lines.append(
            f"Discretionary cash flow consumed: {facts.discretionary_months_consumed:.1f} months"
        )

    if facts.warnings:
        fact_lines.append("Known limitations: " + "; ".join(facts.warnings))

    return fact_lines


def build_purchase_explanation_prompt(facts: PurchaseFacts) -> list[RuntimeMessage]:
    user_prompt = "Explain this purchase's financial impact using only these facts:\n" + "\n".join(
        purchase_fact_lines(facts)
    )

    return [
        RuntimeMessage(role="system", content=_SYSTEM_PROMPT),
        RuntimeMessage(role="user", content=user_prompt),
    ]
