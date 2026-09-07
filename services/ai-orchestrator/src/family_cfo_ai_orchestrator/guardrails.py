from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Collection, Mapping
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


# --- currency-aware money claims (#152 review) ----------------------------------
#
# `find_unattributed_numbers` grounds NUMBERS, which is right for counts and
# months but lets a money figure change units: a tool that discloses an
# excluded EUR 9,000.00 pension grounded "9000.00" for any currency, so
# "USD 9,000.00" passed — a real figure in the wrong unit is the same harm as
# an invented one. `known_money` binds each money figure to the currencies the
# tools reported it in, and a claim that NAMES a currency must match one of
# them. A bare number stays the number check's business.

_CURRENCY_SYMBOLS: dict[str, frozenset[str]] = {
    "$": frozenset({"USD", "CAD", "AUD", "NZD", "SGD", "HKD", "MXN", "TWD"}),
    "€": frozenset({"EUR"}),
    "£": frozenset({"GBP"}),
    "¥": frozenset({"JPY", "CNY"}),
    "₫": frozenset({"VND"}),
    "₹": frozenset({"INR"}),
    "₩": frozenset({"KRW"}),
}
_MONEY_CLAIM_PATTERN = re.compile(
    r"(?P<code_before>\b[A-Z]{3})\s?(?P<n1>\d[\d,]*\.?\d*)"
    r"|(?P<symbol>[$€£¥₫₹₩])\s?(?P<n2>\d[\d,]*\.?\d*)"
    r"|(?P<n3>\d[\d,]*\.?\d*)\s?(?P<code_after>[A-Z]{3})\b"
)


def find_currency_mismatches(
    text: str, known_money: Mapping[str, Collection[str]]
) -> list[str]:
    """Money claims in ``text`` whose named currency contradicts every grounding
    of that figure.

    ``known_money`` maps a grounded number (as `extract_numbers` writes it) to
    the ISO codes the tools reported it in. Only a claim that names a currency —
    an ISO code beside the number, or a symbol — is checked, against the figures
    grounded exactly or within the same ±1% the number check allows. A figure
    grounded in none of them is left to `find_unattributed_numbers`; one grounded
    only in other currencies is a violation, worded so a corrective retry can
    fix the unit rather than the number.
    """
    if not known_money:
        return []
    known: list[tuple[float, str, frozenset[str]]] = []
    for number, currencies in known_money.items():
        try:
            known.append((float(number), number, frozenset(c.upper() for c in currencies)))
        except ValueError:
            continue

    violations: list[str] = []
    for match in _MONEY_CLAIM_PATTERN.finditer(text):
        if match.group("symbol"):
            claimed = _CURRENCY_SYMBOLS[match.group("symbol")]
            raw = match.group("n2")
        elif match.group("code_before"):
            claimed = frozenset({match.group("code_before")})
            raw = match.group("n1")
        else:
            claimed = frozenset({match.group("code_after")})
            raw = match.group("n3")
        number = raw.replace(",", "")
        try:
            value = float(number)
        except ValueError:
            continue
        if abs(value) <= _MATERIAL_THRESHOLD:
            continue
        tolerance = _RELATIVE_TOLERANCE * max(abs(value), 1.0)
        groundings = [
            currencies
            for known_value, known_number, currencies in known
            if known_number == number or abs(known_value - value) <= tolerance
        ]
        if not groundings or any(currencies & claimed for currencies in groundings):
            continue
        actual = " / ".join(sorted(set().union(*groundings)))
        violation = f"{match.group(0).strip()} is grounded only in {actual}"
        if violation not in violations:
            violations.append(violation)
    return violations


def validate_recommendation(
    text: str,
    known_values: set[str],
    *,
    known_money: Mapping[str, Collection[str]] | None = None,
) -> GuardrailResult:
    violations = find_unattributed_numbers(text, known_values)
    if known_money:
        violations = [*violations, *find_currency_mismatches(text, known_money)]
    return GuardrailResult(passed=not violations, violations=violations)
