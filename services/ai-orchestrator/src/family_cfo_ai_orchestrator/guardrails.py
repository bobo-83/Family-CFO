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


def find_unattributed_numbers(text: str, known_values: set[str]) -> list[str]:
    """Return numeric substrings in ``text`` that don't appear in ``known_values``.

    A conservative (string-based, not semantic) check: it exists to catch
    invented figures, not to validate arithmetic. False positives fail closed
    into the deterministic explanation stub rather than risking a fabricated
    number reaching the user (ADR 0003).
    """
    return sorted(number for number in extract_numbers(text) if number not in known_values)


def validate_recommendation(text: str, known_values: set[str]) -> GuardrailResult:
    violations = find_unattributed_numbers(text, known_values)
    return GuardrailResult(passed=not violations, violations=violations)
