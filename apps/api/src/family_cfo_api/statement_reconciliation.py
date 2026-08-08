"""#25: does the synced ledger actually match the statement?

A statement balance says what is owed. Its line items answer the question that
matters more for a household whose whole picture is built on a bank feed: **is
what I'm looking at complete?** A charge on the statement with no synced
transaction is a hole in the ledger — and this app has been bitten by a feed
silently missing history before.

Three outcomes, and the unmatched ones are the point:

  matched            — the line and a synced transaction are the same charge
  missing_from_sync  — on the statement, never delivered by the feed
  not_on_statement   — synced, absent from the statement (usually posted after
                       the cycle closed; occasionally a duplicate)

Matching is deliberately conservative. A wrong match hides a real gap, which is
worse than showing an unmatched pair a person can resolve in one glance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from family_cfo_api.bill_detection import normalize_merchant

# A statement's posting date and the feed's transaction date disagree by a few
# days (weekend, batch posting). Wider than this and "same day-ish" stops being
# evidence at all.
_DATE_WINDOW_DAYS = 5

# Amounts must agree to the cent to call a line matched: a card charge is not a
# variable utility bill. A near-miss is reported as its own kind instead, so a
# tip adjustment or partial capture is visible rather than silently accepted.
_NEAR_AMOUNT_TOLERANCE = 0.05

# Below this, two descriptions aren't the same merchant. Token overlap rather
# than string distance: feeds abbreviate ("SQ *BLUE BOTTLE" vs "Blue Bottle").
_MIN_NAME_SCORE = 0.34


@dataclass(frozen=True, slots=True)
class StatementLine:
    """One row read off the statement."""

    id: str
    occurred_on: date
    description: str
    amount_minor: int  # negative = a charge, matching the ledger's convention


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    id: str
    occurred_at: date
    merchant: str
    amount_minor: int


@dataclass(frozen=True, slots=True)
class LineMatch:
    line_id: str
    transaction_id: str | None
    kind: str | None  # "exact" | "amount_differs"


@dataclass(frozen=True, slots=True)
class Reconciliation:
    matches: list[LineMatch]
    # Ledger transactions in the cycle that no statement line claimed.
    unmatched_transaction_ids: list[str]
    matched_count: int
    missing_from_sync_count: int
    not_on_statement_count: int
    amount_differs_count: int


def _name_score(a: str, b: str) -> float:
    """Token overlap of two merchant strings, 0..1.

    Feeds and statements describe the same merchant differently — prefixes
    ("SQ *", "TST*"), store numbers, truncation. Shared tokens survive all of
    that; exact equality does not.
    """
    left = set(normalize_merchant(a).split())
    right = set(normalize_merchant(b).split())
    if not left or not right:
        return 0.0
    # Jaccard would punish a long feed description that CONTAINS the statement's
    # short one; overlap relative to the smaller side is the right shape here.
    return len(left & right) / min(len(left), len(right))


def _candidate_score(line: StatementLine, txn: LedgerTransaction) -> tuple[float, str] | None:
    """How well a ledger transaction explains a statement line, or None.

    Returns (score, kind). Higher is better; the caller assigns greedily.
    """
    days = abs((txn.occurred_at - line.occurred_on).days)
    if days > _DATE_WINDOW_DAYS:
        return None
    # Both sides use the ledger's sign convention, so a charge matches a charge
    # and a payment/credit matches a payment/credit — never each other.
    if (txn.amount_minor < 0) != (line.amount_minor < 0):
        return None

    exact = txn.amount_minor == line.amount_minor
    if not exact:
        magnitude = abs(line.amount_minor)
        if magnitude == 0:
            return None
        drift = abs(abs(txn.amount_minor) - magnitude) / magnitude
        if drift > _NEAR_AMOUNT_TOLERANCE:
            return None

    name = _name_score(line.description, txn.merchant)
    # An exact amount on a near date is strong evidence by itself; a differing
    # amount must be backed by the merchant name or it isn't the same charge.
    if not exact and name < _MIN_NAME_SCORE:
        return None

    proximity = 1.0 - (days / (_DATE_WINDOW_DAYS + 1))
    score = (2.0 if exact else 0.5) + name + proximity
    return score, ("exact" if exact else "amount_differs")


def reconcile(
    lines: list[StatementLine], transactions: list[LedgerTransaction]
) -> Reconciliation:
    """Pair statement lines with synced transactions, one to one.

    Greedy by descending score: the most convincing pair is taken first, and
    both sides are then consumed, so one transaction can never explain two
    lines (which would understate a genuine gap).
    """
    candidates: list[tuple[float, str, StatementLine, LedgerTransaction]] = []
    for line in lines:
        for txn in transactions:
            scored = _candidate_score(line, txn)
            if scored is not None:
                candidates.append((scored[0], scored[1], line, txn))
    # Sort by score, then by ids so an equal-scoring tie resolves the same way
    # every run — reconciliation must be reproducible.
    candidates.sort(key=lambda c: (-c[0], c[2].id, c[3].id))

    taken_lines: set[str] = set()
    taken_txns: set[str] = set()
    matches: list[LineMatch] = []
    amount_differs = 0
    for _score, kind, line, txn in candidates:
        if line.id in taken_lines or txn.id in taken_txns:
            continue
        taken_lines.add(line.id)
        taken_txns.add(txn.id)
        matches.append(LineMatch(line_id=line.id, transaction_id=txn.id, kind=kind))
        if kind == "amount_differs":
            amount_differs += 1

    for line in lines:
        if line.id not in taken_lines:
            matches.append(LineMatch(line_id=line.id, transaction_id=None, kind=None))

    unmatched_txns = [t.id for t in transactions if t.id not in taken_txns]
    return Reconciliation(
        matches=matches,
        unmatched_transaction_ids=unmatched_txns,
        matched_count=len(taken_lines),
        missing_from_sync_count=len(lines) - len(taken_lines),
        not_on_statement_count=len(unmatched_txns),
        amount_differs_count=amount_differs,
    )


def reconcile_statement(engine, household_id: str, statement_id: str) -> Reconciliation:
    """Run reconciliation for a stored statement and persist the matches.

    Idempotent: every run recomputes from scratch, so a later sync that fills a
    gap turns an unmatched line into a matched one without leaving stale state.
    """
    from family_cfo_api import repository

    statement = repository.get_card_statement(engine, household_id, statement_id)
    if statement is None:
        raise ValueError("statement not found")

    stored = repository.list_statement_lines(engine, household_id, statement_id)
    lines = [
        StatementLine(
            id=row.id,
            occurred_on=row.occurred_on,
            description=row.description,
            amount_minor=row.amount_minor,
        )
        for row in stored
    ]

    # Only the card's own transactions, over the cycle plus the date slack the
    # matcher allows — a charge posted just outside the printed period still
    # belongs to it.
    start = (statement.period_start or statement.due_date) - timedelta(
        days=_DATE_WINDOW_DAYS + 31
    )
    end = (statement.period_end or statement.due_date) + timedelta(days=_DATE_WINDOW_DAYS)
    ledger = [
        LedgerTransaction(
            id=txn.id,
            occurred_at=txn.occurred_at,
            merchant=txn.merchant or txn.description or "",
            amount_minor=txn.amount_minor,
        )
        for txn in repository.list_transactions(
            engine, household_id, limit=100_000, start=start, end=end
        )
        if txn.account_id == statement.account_id
    ]

    result = reconcile(lines, ledger)
    for match in result.matches:
        repository.set_statement_line_match(
            engine, household_id, match.line_id, match.transaction_id, match.kind
        )
    return result
