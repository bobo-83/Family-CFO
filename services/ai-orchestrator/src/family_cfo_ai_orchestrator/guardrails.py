from __future__ import annotations

import re
from bisect import bisect_left
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
# Rounding a grounded figure to significant digits ("$123,456" → "about
# $123,000", or "$120,000" after a small subtraction) is honest reporting; a
# false accept here is bounded to a ≤1% error on a real figure.
_RELATIVE_TOLERANCE = 0.01


def _is_year_like(number: str) -> bool:
    return number.isdigit() and _YEAR_RANGE[0] <= int(number) <= _YEAR_RANGE[1]


def _matches_pair_arithmetic(value: float, sorted_knowns: list[float], tolerance: float) -> bool:
    """True when ``value`` ≈ a+b, a−b, or b−a for grounded values a, b.

    Models narrate purchases with honest arithmetic ("$8,215.64 minus the
    $7,000 laptop leaves $1,215.64") that no single-value tolerance can see.
    Bisect per element keeps this O(n log n). An element may pair with itself.
    """
    for a in sorted_knowns:
        for target in (value - a, a - value, a + value):
            index = bisect_left(sorted_knowns, target)
            for neighbor in (index - 1, index):
                if 0 <= neighbor < len(sorted_knowns) and abs(
                    sorted_knowns[neighbor] - target
                ) <= tolerance:
                    return True
    return False


# "401k", "529", "1099-DIV", "24h", "25x": digits glued to a letter name a
# THING, not an amount of money. They are never a figure the family could act
# on, and treating them as claims made the advisor fail closed for naming an
# account type out loud (the household's own account called "401k" used to
# ground 401 as a side effect; account names no longer ground figures).
_GLUED_TO_LETTER = re.compile(r"[A-Za-z]")
# The same thing where no letter is touching the digits: US account-type and
# tax-form identifiers the advisor has to be able to say out loud ("the 529
# plan", "a 1099-DIV arrives in February"). Bare integers ONLY — an amount is
# written with decimals or separators, so "USD 529.00" is still a money claim
# and still checked.
_NAME_LIKE_IDENTIFIERS = frozenset(
    {"401", "403", "457", "529", "1040", "1095", "1098", "1099", "2555", "4868", "8606"}
)


def find_unattributed_numbers(text: str, known_values: set[str]) -> list[str]:
    """Return numeric substrings in ``text`` not traceable to ``known_values``.

    A conservative (string-based, not semantic) check: it exists to catch
    invented figures, not to validate arithmetic. Immaterial numbers (≤100),
    year-like integers, values within ±1% of a grounded figure (M56), and
    values within ±1% of a sum/difference of two grounded figures (M60) are
    tolerated — a strict verbatim match rejected essentially every
    naturally-phrased answer (rounded totals, "3-6 months" guidance, derived
    remainders). Accepted trade-off: a figure equal to a±b of two real values
    passes even if contextually wrong — it is still composed of the
    household's real numbers, and chronic fallback made the advisor unusable.
    Figures matching no grounded value or pair still fail closed into the
    deterministic explanation stub (ADR 0003).
    """
    known_floats: list[float] = []
    for value in known_values:
        try:
            known_floats.append(float(value))
        except ValueError:
            continue
    known_floats.sort()

    def is_violation(number: str) -> bool:
        if number in known_values:
            return False
        try:
            value = float(number)
        except ValueError:
            return False
        if abs(value) <= _MATERIAL_THRESHOLD or _is_year_like(number):
            return False
        if number in _NAME_LIKE_IDENTIFIERS:
            return False
        tolerance = _RELATIVE_TOLERANCE * max(abs(value), 1.0)
        if any(abs(value - known) <= tolerance for known in known_floats):
            return False
        return not _matches_pair_arithmetic(value, known_floats, tolerance)

    claimed: set[str] = set()
    for match in _NUMBER_PATTERN.finditer(text):
        tail = text[match.end() : match.end() + 1]
        if tail and _GLUED_TO_LETTER.match(tail):
            continue
        claimed.add(match.group(0).replace(",", ""))
    return sorted(number for number in claimed if is_violation(number))


def validate_recommendation(text: str, known_values: set[str]) -> GuardrailResult:
    violations = find_unattributed_numbers(text, known_values)
    return GuardrailResult(passed=not violations, violations=violations)
