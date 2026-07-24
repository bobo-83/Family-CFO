"""The Overview's year view (M-yearly): monthly trend + grounded narrative.

Aggregates reuse the study job's month machinery (income, spending, top
categories) plus reconstructed month-end net worth. The narrative and
improvement suggestions come from the household's own runtime, grounded the
same way chat answers are: every number the model may quote is handed to it
in display form, and the finished text is validated against that set —
a failed validation falls back to a deterministic summary, never to
ungrounded prose (ADR 0009 applies to the year review too).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.engine import Engine

from family_cfo_ai_orchestrator import RuntimeUnavailableError, extract_numbers, validate_recommendation

from family_cfo_api import finance_service, repository
from family_cfo_api.ai_runtime_selection import resolve_ai_config, select_tool_runtime
from family_cfo_api.ai_study import month_bounds
from family_cfo_api.explanation import format_money
from family_cfo_financial_engine import Money

logger = logging.getLogger(__name__)

_NARRATIVE_MAX_TOKENS = 1600
_SUGGESTION_CAP = 4


@dataclass(frozen=True, slots=True)
class YearMonth:
    month: str
    income_minor: int
    spending_minor: int
    net_minor: int
    net_worth_eom_minor: int | None


def year_months(
    engine: Engine, household_id: str, year: int, *, today: date
) -> list[str]:
    """The year's months that overlap the household's data, oldest first,
    capped at the current month (a future month has nothing to show)."""
    earliest, latest = repository.transaction_month_range(engine, household_id)
    if earliest is None:
        return []
    months = []
    for number in range(1, 13):
        month = f"{year:04d}-{number:02d}"
        if month < earliest[:7] or month > latest[:7] or month > today.strftime("%Y-%m"):
            continue
        months.append(month)
    return months


def build_year_overview(
    engine: Engine, household_id: str, currency: str, year: int, *, today: date
) -> tuple[list[YearMonth], list[tuple[str, int]]]:
    """(months, top_categories) — the year view's chart data.

    Month-end net worth is reconstructed from today's balances minus later
    transactions (approximate before daily snapshots existed; the same
    approach the debt history uses)."""
    months: list[YearMonth] = []
    category_totals: dict[str, int] = {}
    category_names = {c.id: c.name for c in repository.list_categories(engine, household_id)}
    for month in year_months(engine, household_id, year, today=today):
        start, end = month_bounds(month)
        # Detected income deposits (ADR 0054), not the Income category alone —
        # households rarely hand-file paychecks. The hand-filed ones still
        # count via the category when detection recognized them as income.
        income = max(
            finance_service.income_received_between(engine, household_id, currency, start, end),
            repository.sum_income(engine, household_id, start, end, currency),
        )
        spending = repository.sum_spending(engine, household_id, start, end, currency)
        eom = min(end, today)
        net_worth = finance_service.reconstruct_net_worth(engine, household_id, eom, currency)
        months.append(
            YearMonth(
                month=month,
                income_minor=income,
                spending_minor=spending,
                net_minor=income - spending,
                net_worth_eom_minor=net_worth,
            )
        )
        by_category = repository.sum_spending_by_category(engine, household_id, start, end, currency)
        for category_id, amount_minor in by_category.items():
            name = category_names.get(category_id, "Other")
            category_totals[name] = category_totals.get(name, 0) + amount_minor
    top = sorted(category_totals.items(), key=lambda kv: kv[1], reverse=True)[:8]
    return months, top


def _deterministic_review(
    months: list[YearMonth], top: list[tuple[str, int]], currency: str, year: int
) -> tuple[str, list[str]]:
    """The fallback narrative: correct, plain, and entirely computed."""
    if not months:
        return (f"No transaction data recorded for {year} yet.", [])
    income = sum(m.income_minor for m in months)
    spending = sum(m.spending_minor for m in months)
    net = income - spending
    best = max(months, key=lambda m: m.net_minor)
    worst = min(months, key=lambda m: m.net_minor)
    money = lambda minor: format_money(Money(minor, currency))  # noqa: E731
    summary = (
        f"Across {len(months)} months of {year}, the household brought in "
        f"{money(income)} and spent {money(spending)}, keeping {money(net)}. "
        f"The strongest month was {best.month} ({money(best.net_minor)} kept); "
        f"the tightest was {worst.month} ({money(worst.net_minor)})."
    )
    suggestions = []
    if top:
        name, amount = top[0]
        suggestions.append(
            f"{name} was the largest spending category at {money(amount)} — "
            "worth a look for recurring costs that could shrink."
        )
    if net < 0:
        suggestions.append(
            "Spending outpaced income for the year so far — the advisor's "
            "find-savings tool can point at specific cuts."
        )
    return summary, suggestions


def _grounded_facts(
    months: list[YearMonth], top: list[tuple[str, int]], currency: str
) -> str:
    money = lambda minor: format_money(Money(minor, currency))  # noqa: E731
    lines = []
    for m in months:
        lines.append(
            f"{m.month}: income {money(m.income_minor)}, spending {money(m.spending_minor)}, "
            f"kept {money(m.net_minor)}"
            + (f", net worth {money(m.net_worth_eom_minor)}" if m.net_worth_eom_minor is not None else "")
        )
    income = sum(m.income_minor for m in months)
    spending = sum(m.spending_minor for m in months)
    lines.append(
        f"Year totals: income {money(income)}, spending {money(spending)}, kept {money(income - spending)}"
    )
    for name, amount in top:
        lines.append(f"Category total: {name} {money(amount)}")
    return "\n".join(lines)


_REVIEW_PROMPT = (
    "You are a family's financial advisor writing their year-in-review. "
    "Using ONLY the month-by-month facts provided (quote figures exactly as "
    "written there — never compute new numbers), write: first a warm, honest "
    "3-5 sentence summary of how the year is going (trends, best/worst "
    "stretches, direction of net worth); then the line 'SUGGESTIONS:' followed "
    f"by up to {_SUGGESTION_CAP} short, concrete, actionable bullet lines (each "
    "starting with '- ') on what could be improved. Plain language, no "
    "headings other than SUGGESTIONS:, no disclaimers."
)


def _parse_review(text: str) -> tuple[str, list[str]]:
    head, _, tail = text.partition("SUGGESTIONS:")
    summary = head.strip()
    suggestions = [
        line.lstrip("- ").strip()
        for line in tail.strip().splitlines()
        if line.strip().startswith("-")
    ][:_SUGGESTION_CAP]
    return summary, suggestions


def generate_review(
    engine: Engine,
    household_id: str,
    currency: str,
    year: int,
    *,
    today: date,
) -> dict:
    """Generate (and cache) the year review. Grounded or deterministic — the
    stored narrative never contains a number that isn't in the facts."""
    months, top = build_year_overview(engine, household_id, currency, year, today=today)
    summary, suggestions = _deterministic_review(months, top, currency, year)
    model_used: str | None = None

    runtime = select_tool_runtime(engine, household_id)
    if runtime is not None and months:
        facts = _grounded_facts(months, top, currency)
        try:
            from family_cfo_ai_orchestrator import RuntimeMessage

            messages = [
                RuntimeMessage(role="system", content=_REVIEW_PROMPT),
                RuntimeMessage(role="user", content=facts),
            ]
            completion = runtime.complete(
                messages, temperature=0.4, max_tokens=_NARRATIVE_MAX_TOKENS
            )
            text = (completion.text or "").strip()
            if not text:
                # A reasoning model can think the whole budget away (the chat
                # loop nudges the same way — ADR 0058).
                completion = runtime.complete(
                    [
                        *messages,
                        RuntimeMessage(role="assistant", content=""),
                        RuntimeMessage(
                            role="user",
                            content=(
                                "Your reply was empty. Write the summary and the "
                                "SUGGESTIONS: bullets now, briefly, without "
                                "further deliberation."
                            ),
                        ),
                    ],
                    temperature=0.4,
                    max_tokens=_NARRATIVE_MAX_TOKENS,
                )
                text = (completion.text or "").strip()
            candidate_summary, candidate_suggestions = _parse_review(text)
            known = extract_numbers(facts)
            guardrail = validate_recommendation(
                candidate_summary + "\n" + "\n".join(candidate_suggestions), known
            )
            if candidate_summary and guardrail.passed:
                summary, suggestions = candidate_summary, candidate_suggestions
                model_used = resolve_ai_config(engine, household_id).model or None
            else:
                logger.warning(
                    "yearly review rejected (empty or ungrounded %s); using deterministic",
                    getattr(guardrail, "violations", None),
                )
        except RuntimeUnavailableError:
            logger.warning("yearly review runtime unavailable; using deterministic")
        finally:
            runtime.close()

    repository.upsert_yearly_review(
        engine,
        household_id=household_id,
        year=year,
        summary=summary,
        suggestions=suggestions,
        months_covered=len(months),
        model=model_used,
    )
    return {
        "summary": summary,
        "suggestions": suggestions,
        "months_covered": len(months),
        "model": model_used,
    }
