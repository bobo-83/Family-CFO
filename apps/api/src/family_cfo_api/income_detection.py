"""Income detection from recurring checking-account deposits (M61).

The inflow mirror of ``bill_detection``: deposits grouped by normalized payer,
clustered into a pay cadence. Every detected source carries its underlying
transactions so the family can audit the evidence and remove or add individual
deposits (persisted in ``income_transaction_overrides``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median

from family_cfo_api.bill_detection import _classify_cadence, normalize_merchant

# Deposits vary more than subscription charges (overtime, bonuses on top of a
# base salary), so income tolerates wider amount spread than bills.
AMOUNT_TOLERANCE = 0.50


@dataclass(frozen=True, slots=True)
class IncomeTransaction:
    """The slice of an inflow transaction that detection needs."""

    id: str
    occurred_at: date
    amount_minor: int  # positive = inflow
    currency: str
    merchant: str | None
    description: str | None
    # The checking account the deposit landed in (M62 evidence detail).
    account_name: str = ""

    @property
    def display_name(self) -> str:
        return (self.merchant or self.description or "Deposit").strip()[:120]


@dataclass(frozen=True, slots=True)
class IncomeSourceCandidate:
    source_key: str
    name: str
    frequency: str
    currency: str
    typical_amount_minor: int  # median deposit
    transactions: list[IncomeTransaction]  # oldest first — the evidence


def detect_income_sources(
    transactions: list[IncomeTransaction],
    *,
    excluded_ids: set[str] | None = None,
) -> list[IncomeSourceCandidate]:
    """Recurring deposit groups, largest typical amount first.

    ``excluded_ids`` (user "remove" verdicts) are dropped BEFORE cadence
    analysis so an excluded outlier cannot block an otherwise-regular payer.
    """
    excluded = excluded_ids or set()
    groups: dict[tuple[str, str], list[IncomeTransaction]] = {}
    for txn in transactions:
        if txn.amount_minor <= 0 or txn.id in excluded:
            continue
        key = normalize_merchant(txn.merchant) or normalize_merchant(txn.description)
        if not key:
            continue
        groups.setdefault((key, txn.currency), []).append(txn)

    candidates: list[IncomeSourceCandidate] = []
    for (key, currency), group in groups.items():
        group.sort(key=lambda t: t.occurred_at)
        dates = sorted({t.occurred_at for t in group})
        frequency = _classify_cadence(dates)
        if frequency is None:
            continue
        amounts = [t.amount_minor for t in group]
        typical = int(median(amounts))
        if typical <= 0 or any(
            abs(amount - typical) > typical * AMOUNT_TOLERANCE for amount in amounts
        ):
            continue
        candidates.append(
            IncomeSourceCandidate(
                source_key=key,
                name=group[-1].display_name,
                frequency=frequency,
                currency=currency,
                typical_amount_minor=typical,
                transactions=group,
            )
        )

    candidates.sort(key=lambda c: (-c.typical_amount_minor, c.name.lower()))
    return candidates
