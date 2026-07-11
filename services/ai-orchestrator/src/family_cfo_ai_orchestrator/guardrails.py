from __future__ import annotations

import re
from dataclasses import dataclass, field

from family_cfo_ai_orchestrator.prompts import PurchaseFacts, ReportFacts, purchase_fact_lines, report_fact_lines

_NUMBER_PATTERN = re.compile(r"\d[\d,]*\.?\d*")


@dataclass(frozen=True, slots=True)
class GuardrailResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def extract_numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in _NUMBER_PATTERN.finditer(text)}


def _known_values_from_lines(lines: list[str]) -> set[str]:
    known: set[str] = set()
    for line in lines:
        known |= extract_numbers(line)
    return known


def known_values_from_facts(facts: PurchaseFacts) -> set[str]:
    """The set of numbers actually sent to the model, for cross-checking its response."""
    return _known_values_from_lines(purchase_fact_lines(facts))


def known_values_from_report_facts(facts: ReportFacts) -> set[str]:
    """The set of numbers actually sent to the model, for cross-checking a report explanation."""
    return _known_values_from_lines(report_fact_lines(facts))


# The harm model (ADR 0003) is fabricated *money* figures. Numbers below this
# are months, counts, percentages, and ratios ("3-6 months", "0.1% of net
# worth") — honest conversational arithmetic, not amounts a user would act on.
_MATERIAL_THRESHOLD = 100.0
_YEAR_RANGE = (1900, 2100)
# Rounding a grounded figure to significant digits ("$979,278" → "about
# $974,000" after a small subtraction, "$980,000") is honest reporting; a
# false accept here is bounded to a ≤1% error on a real figure.
_RELATIVE_TOLERANCE = 0.01


def _is_year_like(number: str) -> bool:
    return number.isdigit() and _YEAR_RANGE[0] <= int(number) <= _YEAR_RANGE[1]


def find_unattributed_numbers(text: str, known_values: set[str]) -> list[str]:
    """Return numeric substrings in ``text`` not traceable to ``known_values``.

    A conservative (string-based, not semantic) check: it exists to catch
    invented figures, not to validate arithmetic. Immaterial numbers (<100),
    year-like integers, and values within ±1% of a grounded figure are
    tolerated (M56) — a strict verbatim match rejected essentially every
    naturally-phrased answer (rounded totals, "3-6 months" guidance, derived
    ratios). Material figures with no nearby grounded value still fail closed
    into the deterministic explanation stub rather than risking a fabricated
    number reaching the user (ADR 0003).
    """
    known_floats: list[float] = []
    for value in known_values:
        try:
            known_floats.append(float(value))
        except ValueError:
            continue

    def is_violation(number: str) -> bool:
        if number in known_values:
            return False
        try:
            value = float(number)
        except ValueError:
            return False
        if abs(value) < _MATERIAL_THRESHOLD or _is_year_like(number):
            return False
        return not any(
            abs(value - known) <= _RELATIVE_TOLERANCE * max(abs(known), 1.0)
            for known in known_floats
        )

    return sorted(number for number in extract_numbers(text) if is_violation(number))


def validate_recommendation(text: str, known_values: set[str]) -> GuardrailResult:
    violations = find_unattributed_numbers(text, known_values)
    return GuardrailResult(passed=not violations, violations=violations)
