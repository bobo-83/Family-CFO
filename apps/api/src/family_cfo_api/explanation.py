from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from family_cfo_financial_engine import Money


@dataclass(frozen=True, slots=True)
class PurchaseExplanationContext:
    item: str
    price: Money
    net_worth_after: Money
    emergency_fund_months_before: float | None
    emergency_fund_months_after: float | None
    discretionary_months_consumed: float | None
    warnings: list[str]


class ExplanationAdapter(Protocol):
    """The seam between deterministic calculation output and prose explanation.

    M3 ships only ``DeterministicExplanationAdapter``. M4 adds a vLLM-backed
    adapter behind this same interface (ADR 0007), without changing callers.
    """

    def explain_purchase(self, context: PurchaseExplanationContext) -> str: ...


def format_money(amount: Money) -> str:
    sign = "-" if amount.amount_minor < 0 else ""
    major_units = abs(amount.amount_minor) / 100
    return f"{sign}{amount.currency} {major_units:,.2f}"


class DeterministicExplanationAdapter:
    """No-model explanation stub: renders calculation outputs as plain sentences."""

    source = "deterministic_stub"

    def explain_purchase(self, context: PurchaseExplanationContext) -> str:
        sentences = [
            f"Buying {context.item} for {format_money(context.price)} would leave "
            f"your net worth at {format_money(context.net_worth_after)}."
        ]

        if (
            context.emergency_fund_months_before is not None
            and context.emergency_fund_months_after is not None
        ):
            sentences.append(
                "Your emergency fund coverage would move from "
                f"{context.emergency_fund_months_before:.1f} to "
                f"{context.emergency_fund_months_after:.1f} months."
            )

        if context.discretionary_months_consumed is not None:
            sentences.append(
                "This purchase equals about "
                f"{context.discretionary_months_consumed:.1f} months of your discretionary cash flow."
            )

        if context.warnings:
            sentences.append("Note: " + " ".join(context.warnings))

        return " ".join(sentences)
