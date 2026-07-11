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

# M63: a checking inflow matching an outflow of the same amount in ANY of the
# household's accounts within this many days is an internal transfer.
TRANSFER_MATCH_DAYS = 3

_TRANSFER_TEXT_MARKERS = ("internal transfer",)


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


def is_internal_transfer(
    txn: IncomeTransaction, outflows_by_amount: dict[int, list[date]]
) -> bool:
    """True when a deposit is the household's own money changing accounts.

    Either the bank labels it ("internal transfer"), or an outflow of the
    same amount left a sibling account within TRANSFER_MATCH_DAYS — the money
    demonstrably came from inside the household, so it is not income.
    """
    text = f"{txn.merchant or ''} {txn.description or ''}".lower()
    if any(marker in text for marker in _TRANSFER_TEXT_MARKERS):
        return True
    for outflow_date in outflows_by_amount.get(txn.amount_minor, ()):
        if abs((outflow_date - txn.occurred_at).days) <= TRANSFER_MATCH_DAYS:
            return True
    return False


# M65: adjacent sorted amounts more than this far apart start a new cluster —
# a $2,830.78 paycheck must not share a group with a $23,124 one-off just
# because the bank labels both "Online Transfer".
CLUSTER_GAP = 0.30


def _cluster_by_amount(group: list[IncomeTransaction]) -> list[list[IncomeTransaction]]:
    """Split a merchant group into amount bands (greedy over sorted amounts)."""
    ordered = sorted(group, key=lambda t: t.amount_minor)
    clusters: list[list[IncomeTransaction]] = [[ordered[0]]]
    for txn in ordered[1:]:
        if txn.amount_minor > clusters[-1][-1].amount_minor * (1 + CLUSTER_GAP):
            clusters.append([txn])
        else:
            clusters[-1].append(txn)
    return clusters


def detect_income_sources(
    transactions: list[IncomeTransaction],
    *,
    excluded_ids: set[str] | None = None,
) -> list[IncomeSourceCandidate]:
    """Recurring deposit groups, largest typical amount first.

    ``excluded_ids`` (user "remove" verdicts) are dropped BEFORE cadence
    analysis so an excluded outlier cannot block an otherwise-regular payer.
    Merchant groups are first split into amount bands (M65) so a regular
    paycheck is detected even when unrelated transfers share its bank label.
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
        clusters = _cluster_by_amount(group)
        for index, cluster in enumerate(clusters):
            cluster.sort(key=lambda t: t.occurred_at)
            dates = sorted({t.occurred_at for t in cluster})
            frequency = _classify_cadence(dates)
            if frequency is None:
                continue
            amounts = [t.amount_minor for t in cluster]
            typical = int(median(amounts))
            if typical <= 0 or any(
                abs(amount - typical) > typical * AMOUNT_TOLERANCE for amount in amounts
            ):
                continue
            name = cluster[-1].display_name
            if len(clusters) > 1:
                # Disambiguate multiple bands under one bank label.
                name = f"{name} (~{typical / 100:,.2f} {currency})"[:120]
            candidates.append(
                IncomeSourceCandidate(
                    source_key=key if len(clusters) == 1 else f"{key}#{index}",
                    name=name,
                    frequency=frequency,
                    currency=currency,
                    typical_amount_minor=typical,
                    transactions=cluster,
                )
            )

    candidates.sort(key=lambda c: (-c.typical_amount_minor, c.name.lower()))
    return candidates
