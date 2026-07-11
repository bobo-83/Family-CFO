"""Bill suggestions from recurring transactions (M58).

Combs the outflows of checking/credit-card accounts for recurring charges the
family could confirm as bills. Deliberately deterministic pattern-matching,
not an LLM guess (ADR 0003): every suggestion is explainable as "N charges of
~$X at ~cadence", and confirming one only prefills the normal bill form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from family_cfo_api.finance_service import add_months

# How far back detection looks. Thirteen months so an annual charge can show
# up twice (e.g. an insurance premium paid last July and this July).
LOOKBACK_DAYS = 400

# A group's amounts must all sit within this fraction of their median —
# utilities wobble, but a merchant whose charges double is not one bill.
AMOUNT_TOLERANCE = 0.30

# (frequency, min interval days, max interval days, min occurrences).
# Short cadences demand three sightings; quarterly/annual would need most of
# a year to reach three, so two consistent sightings suffice.
_CADENCES = (
    ("weekly", 6, 8, 3),
    ("biweekly", 12, 16, 3),
    ("monthly", 27, 33, 3),
    ("quarterly", 84, 97, 2),
    ("annual", 350, 380, 2),
)

# Interval jitter allowed around the median interval per cadence.
_JITTER_DAYS = {"weekly": 2, "biweekly": 3, "monthly": 5, "quarterly": 10, "annual": 20}

_NON_ALPHA = re.compile(r"[^a-z]+")


@dataclass(frozen=True, slots=True)
class DetectionTransaction:
    """The slice of a transaction that detection needs."""

    occurred_at: date
    amount_minor: int  # negative = outflow (repository convention)
    currency: str
    merchant: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class BillCandidate:
    merchant_key: str
    name: str
    amount_minor: int  # positive (bill amounts are positive)
    currency: str
    frequency: str
    next_due_date: date
    occurrences: int
    last_seen: date


def normalize_merchant(text: str | None) -> str:
    """Collapse a raw merchant string to a stable grouping key.

    "NETFLIX.COM *4029" and "Netflix.com" both become "netflix com" — store
    numbers, dates, and punctuation vary per charge; the letters don't.
    """
    if not text:
        return ""
    return _NON_ALPHA.sub(" ", text.lower()).strip()


def _classify_cadence(dates: list[date]) -> str | None:
    """The cadence bucket the sorted charge dates fall into, or None."""
    intervals = [(b - a).days for a, b in zip(dates, dates[1:])]
    if not intervals:
        return None
    typical = median(intervals)
    for frequency, low, high, min_occurrences in _CADENCES:
        if low <= typical <= high and len(dates) >= min_occurrences:
            jitter = _JITTER_DAYS[frequency]
            if all(abs(interval - typical) <= jitter for interval in intervals):
                return frequency
    return None


def _next_due(last_seen: date, frequency: str) -> date:
    if frequency == "weekly":
        return last_seen + timedelta(days=7)
    if frequency == "biweekly":
        return last_seen + timedelta(days=14)
    if frequency == "monthly":
        return add_months(last_seen, 1)
    if frequency == "quarterly":
        return add_months(last_seen, 3)
    return add_months(last_seen, 12)


def detect_bill_candidates(transactions: list[DetectionTransaction]) -> list[BillCandidate]:
    """Recurring-charge candidates, most frequent first.

    Only outflows participate. A merchant group qualifies when its charge
    dates cluster into one cadence bucket and its amounts stay within
    ±AMOUNT_TOLERANCE of their median; the suggested amount is that median
    (a stable predictor for lightly-varying charges like utilities).
    """
    groups: dict[tuple[str, str], list[DetectionTransaction]] = {}
    for txn in transactions:
        if txn.amount_minor >= 0:
            continue
        key = normalize_merchant(txn.merchant) or normalize_merchant(txn.description)
        if not key:
            continue
        groups.setdefault((key, txn.currency), []).append(txn)

    candidates: list[BillCandidate] = []
    for (key, currency), group in groups.items():
        group.sort(key=lambda t: t.occurred_at)
        # One charge per day: a same-day split (e.g. partial refunds) would
        # otherwise fake a zero-day interval.
        by_day: dict[date, int] = {}
        for txn in group:
            by_day[txn.occurred_at] = by_day.get(txn.occurred_at, 0) + abs(txn.amount_minor)
        dates = sorted(by_day)
        frequency = _classify_cadence(dates)
        if frequency is None:
            continue
        amounts = [by_day[d] for d in dates]
        typical_amount = int(median(amounts))
        if typical_amount <= 0 or any(
            abs(amount - typical_amount) > typical_amount * AMOUNT_TOLERANCE
            for amount in amounts
        ):
            continue
        last_seen = dates[-1]
        raw_name = group[-1].merchant or group[-1].description or key
        candidates.append(
            BillCandidate(
                merchant_key=key,
                name=raw_name.strip()[:120],
                amount_minor=typical_amount,
                currency=currency,
                frequency=frequency,
                next_due_date=_next_due(last_seen, frequency),
                occurrences=len(dates),
                last_seen=last_seen,
            )
        )

    candidates.sort(key=lambda c: (-c.occurrences, c.name.lower()))
    return candidates
