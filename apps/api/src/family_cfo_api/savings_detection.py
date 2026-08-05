"""Recurring savings-contribution detection (#201).

The mirror of bill detection: instead of recurring money leaving for a
merchant, this finds recurring money moving to a SAVINGS VEHICLE the family
owns — $500 a month from checking to a 529 — and calls it what it is.

Composed from pieces that already exist: transfer pairing (an outflow whose
equal-and-opposite leg lands in another of the household's accounts), the
bill detector's cadence classifier, and the account-type → asset-category map
that already knows a 529 is education and a 401(k) is retirement.

Honesty constraints this module is built around (#201):
  * Payroll-deducted retirement contributions NEVER appear here — they don't
    touch the bank feed. Anything reporting "you save X" from these results
    must say it covers transfers only, or the compensation profile's figure
    will silently contradict it.
  * Money moved and moved back is churn, not saving. Reverse flows over the
    window are netted out before a pattern is considered.
  * Transfers are already excluded from spending, so this adds positive
    recognition only — it must never also reduce spending a second time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from family_cfo_api.bill_detection import _classify_cadence, _next_due, normalize_merchant

#: Account types that mean "money put aside", mapped to how the app already
#: categorises them (finance_service.ASSET_CATEGORY_BY_TYPE).
SAVINGS_DESTINATION_TYPES = frozenset(
    {"savings", "529", "retirement", "hsa", "brokerage"}
)

#: How far back to look for a pattern. Matches bill detection so a quarterly
#: or annual contribution has room to show up more than once.
LOOKBACK_DAYS = 400

#: Contribution amounts wobble (a payroll-driven transfer rounds differently,
#: a user bumps $500 to $550). Same tolerance bills use.
AMOUNT_TOLERANCE = 0.30

#: Legs of one transfer land within a few days of each other.
TRANSFER_MATCH_DAYS = 3

#: Below this a "contribution" is noise, not a savings habit.
MIN_CONTRIBUTION_MINOR = 2_000  # $20


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """The slice of a transaction detection needs, plus its account."""

    transaction_id: str
    account_id: str
    occurred_at: date
    amount_minor: int  # negative = outflow (repository convention)
    currency: str
    # #207: the outflow's own label — often the only evidence of where the
    # money went when the destination account is not synced.
    label: str | None = None


@dataclass(frozen=True, slots=True)
class SavingsAccount:
    """A destination candidate: one of the household's own accounts."""

    account_id: str
    name: str
    account_type: str


@dataclass(frozen=True, slots=True)
class ContributionCandidate:
    """A recurring transfer into a savings vehicle, ready to be confirmed."""

    source_account_id: str
    # None when only the outflow is visible and the destination was inferred
    # from the description rather than a matched arrival (#207).
    destination_account_id: str | None
    destination_name: str
    destination_type: str
    amount_minor: int  # positive: what leaves the source each time
    currency: str
    frequency: str
    occurrences: int
    last_seen: date
    next_expected: date
    # False when both legs were seen; True when only the outflow exists and the
    # destination came from a learned route or a name match (#207).
    inferred: bool = False


def _paired_transfers(
    entries: list[LedgerEntry], savings_ids: set[str]
) -> list[tuple[LedgerEntry, LedgerEntry]]:
    """(outflow, inflow) pairs where money left one account and an equal
    amount arrived in a SAVINGS account within a few days.

    Same rule the transactions API uses to show source → destination: equal
    magnitude, opposite sign, different accounts, close in time.
    """
    inflows_by_amount: dict[int, list[LedgerEntry]] = {}
    for entry in entries:
        if entry.amount_minor > 0 and entry.account_id in savings_ids:
            inflows_by_amount.setdefault(entry.amount_minor, []).append(entry)

    pairs: list[tuple[LedgerEntry, LedgerEntry]] = []
    claimed: set[str] = set()
    for entry in entries:
        if entry.amount_minor >= 0 or entry.account_id in savings_ids:
            continue
        magnitude = -entry.amount_minor
        if magnitude < MIN_CONTRIBUTION_MINOR:
            continue
        for candidate in inflows_by_amount.get(magnitude, ()):
            if (
                candidate.transaction_id not in claimed
                and candidate.account_id != entry.account_id
                and candidate.currency == entry.currency
                and abs((candidate.occurred_at - entry.occurred_at).days)
                <= TRANSFER_MATCH_DAYS
            ):
                claimed.add(candidate.transaction_id)
                pairs.append((entry, candidate))
                break
    return pairs


def _net_of_reverse_flows(
    outflows: list[LedgerEntry],
    entries: list[LedgerEntry],
    source_id: str,
    destination_id: str,
) -> bool:
    """False when the destination sent at least as much back to the source as
    it received — money shuttled out and back is churn, not saving (#201)."""
    contributed = sum(-outflow.amount_minor for outflow in outflows)
    returned = 0
    for entry in entries:
        # An outflow FROM the savings account with a matching inflow to the
        # source is the reverse leg.
        if entry.account_id != destination_id or entry.amount_minor >= 0:
            continue
        magnitude = -entry.amount_minor
        for other in entries:
            if (
                other.account_id == source_id
                and other.amount_minor == magnitude
                and abs((other.occurred_at - entry.occurred_at).days)
                <= TRANSFER_MATCH_DAYS
            ):
                returned += magnitude
                break
    return returned < contributed


def _names_match(a: str, b: str) -> bool:
    """Fuzzy merchant-key match: equal, substring, or token-subset. Same rule
    the payment timeline uses to pair a bill with its charge."""
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def _attribute_unpaired_outflows(
    entries: list[LedgerEntry],
    savings: dict[str, SavingsAccount],
    paired: list[tuple[LedgerEntry, LedgerEntry]],
    excluded_keys: set[str],
) -> dict[tuple[str, str], list[LedgerEntry]]:
    """#207: outflows whose ARRIVAL was never synced, attributed to a savings
    destination by evidence available on the outflow alone.

    Two high-confidence signals only — deliberately not generic keywords,
    which would guess at the user with no way to be corrected (that waits for
    the confirm/dismiss channel):

      1. A LEARNED ROUTE: this source has paired with this destination before
         for a similar amount, so later unmatched outflows of that size are
         very likely the same standing transfer.
      2. A NAME MATCH: the outflow's own label names one of the household's
         savings accounts ("Transfer to College 529").

    Anything matching a known bill or liability label is excluded outright —
    rent and card payments are recurring outflows too.
    """
    claimed = {outflow.transaction_id for outflow, _ in paired}

    learned: dict[str, list[tuple[str, int]]] = {}
    for outflow, inflow in paired:
        learned.setdefault(outflow.account_id, []).append(
            (inflow.account_id, -outflow.amount_minor)
        )

    by_name = {
        account_id: normalize_merchant(account.name)
        for account_id, account in savings.items()
    }

    attributed: dict[tuple[str, str], list[LedgerEntry]] = {}
    for entry in entries:
        if (
            entry.amount_minor >= 0
            or entry.transaction_id in claimed
            or entry.account_id in savings  # moving between savings vehicles
        ):
            continue
        magnitude = -entry.amount_minor
        if magnitude < MIN_CONTRIBUTION_MINOR:
            continue
        key = normalize_merchant(entry.label)
        if key and any(_names_match(key, excluded) for excluded in excluded_keys):
            continue

        destination_id = None
        for candidate_id, amount in learned.get(entry.account_id, ()):
            if abs(magnitude - amount) <= amount * AMOUNT_TOLERANCE:
                destination_id = candidate_id
                break
        if destination_id is None and key:
            for candidate_id, name_key in by_name.items():
                if name_key and _names_match(key, name_key):
                    destination_id = candidate_id
                    break
        if destination_id is None:
            continue
        attributed.setdefault((entry.account_id, destination_id), []).append(entry)
    return attributed


def detect_contributions(
    entries: list[LedgerEntry],
    accounts: list[SavingsAccount],
    *,
    today: date | None = None,
    excluded_labels: list[str] | None = None,
) -> list[ContributionCandidate]:
    """Recurring transfers into savings vehicles, largest first.

    Pure: give it a ledger and the household's accounts, get candidates. No
    database, so the rules above are directly testable.
    """
    today = today or date.today()
    horizon = today - timedelta(days=LOOKBACK_DAYS)
    entries = [e for e in entries if e.occurred_at >= horizon]

    savings = {
        account.account_id: account
        for account in accounts
        if account.account_type in SAVINGS_DESTINATION_TYPES
    }
    if not savings:
        return []

    pairs = _paired_transfers(entries, set(savings))

    # Group by the route the money takes, then by similar amount within it.
    by_route: dict[tuple[str, str], list[LedgerEntry]] = {}
    for outflow, inflow in pairs:
        by_route.setdefault((outflow.account_id, inflow.account_id), []).append(outflow)

    # #207: most savings destinations are not synced, so the arrival often
    # never appears. Fold in outflows attributed by learned route or name.
    excluded_keys = {
        normalize_merchant(label) for label in (excluded_labels or []) if label
    }
    inferred_routes = _attribute_unpaired_outflows(
        entries, savings, pairs, excluded_keys
    )
    inferred_only: set[tuple[str, str]] = set()
    for route, outflows in inferred_routes.items():
        if route not in by_route:
            inferred_only.add(route)
        by_route.setdefault(route, []).extend(outflows)

    candidates: list[ContributionCandidate] = []
    for (source_id, destination_id), route_outflows in by_route.items():
        amounts = [-outflow.amount_minor for outflow in route_outflows]
        typical = median(amounts)
        consistent = [
            outflow
            for outflow, amount in zip(route_outflows, amounts, strict=True)
            if abs(amount - typical) <= typical * AMOUNT_TOLERANCE
        ]
        if len(consistent) < 2:
            continue

        dates = sorted(outflow.occurred_at for outflow in consistent)
        frequency = _classify_cadence(dates)
        if frequency is None:
            continue
        if not _net_of_reverse_flows(consistent, entries, source_id, destination_id):
            continue

        destination = savings[destination_id]
        last_seen = dates[-1]
        candidates.append(
            ContributionCandidate(
                source_account_id=source_id,
                destination_account_id=destination_id,
                destination_name=destination.name,
                destination_type=destination.account_type,
                amount_minor=int(
                    median([-outflow.amount_minor for outflow in consistent])
                ),
                currency=consistent[0].currency,
                frequency=frequency,
                occurrences=len(consistent),
                last_seen=last_seen,
                next_expected=_next_due(last_seen, frequency),
                inferred=(source_id, destination_id) in inferred_only,
            )
        )

    candidates.sort(key=lambda c: c.amount_minor, reverse=True)
    return candidates


def monthly_equivalent_minor(candidate: ContributionCandidate) -> int:
    """A contribution's monthly run-rate, so cadences can be summed together."""
    per_year = {
        "weekly": 52, "biweekly": 26, "monthly": 12,
        "quarterly": 4, "semiannual": 2, "annual": 1,
    }[candidate.frequency]
    return round(candidate.amount_minor * per_year / 12)


def detect_for_household(
    engine, household_id: str, *, today: date | None = None
) -> list[ContributionCandidate]:
    """Run detection over a household's real ledger. Thin: the rules live in
    detect_contributions, which stays pure and directly testable."""
    from family_cfo_api import repository

    today = today or date.today()
    since = today - timedelta(days=LOOKBACK_DAYS)
    records = repository.list_transactions(
        engine, household_id, limit=1_000_000, start=since, end=today
    )
    entries = [
        LedgerEntry(
            transaction_id=record.id,
            account_id=record.account_id,
            occurred_at=record.occurred_at,
            amount_minor=record.amount_minor,
            currency=record.currency,
            label=record.merchant or record.description,
        )
        for record in records
    ]
    # name/type maps, NOT list_account_balances: that one inner-joins balance
    # snapshots, so a newly linked 529 with no balance yet would be invisible
    # to detection even while transfers into it pile up.
    names = repository.account_name_map(engine, household_id)
    types = repository.account_type_map(engine, household_id)
    accounts = [
        SavingsAccount(
            account_id=account_id,
            name=names.get(account_id, "Savings"),
            account_type=account_type,
        )
        for account_id, account_type in types.items()
    ]
    # #207: a recurring outflow that matches a known bill or a liability
    # account is that obligation, not saving — rent and card payments recur
    # just as faithfully as a 529 contribution.
    excluded = [bill.name for bill in repository.list_bills(engine, household_id)]
    excluded += [
        names[account_id]
        for account_id, account_type in types.items()
        if account_type in repository.LIABILITY_ACCOUNT_TYPES and account_id in names
    ]
    return detect_contributions(
        entries, accounts, today=today, excluded_labels=excluded
    )
