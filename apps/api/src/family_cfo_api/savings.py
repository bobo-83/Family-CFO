"""Waste-first savings opportunities for the advisor (ADR 0047).

A seasoned budget coach separates needs from wants, finds the leakage
(forgotten/duplicate subscriptions, category creep), and suggests trimming the
LEAST-valued spending first — never the things the family clearly enjoys. This
module computes those signals deterministically; the advisor turns them into
tasteful, goal-linked suggestions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.engine import Engine

from family_cfo_api import bill_detection, finance_service, repository
from family_cfo_financial_engine import Money

# Categories that are needs, not wants. Matched as substrings of the normalized
# category name; everything else is treated as discretionary (a want).
_ESSENTIAL_KEYWORDS = (
    "rent", "mortgage", "housing", "hoa", "utilit", "electric", "water", "sewer",
    "internet", "phone", "mobile", "insurance", "grocer", "medical", "health",
    "pharmacy", "doctor", "dental", "childcare", "daycare", "tuition", "school",
    "transport", "transit", "fuel", "gas", "car payment", "loan", "debt", "tax",
)

# Streaming / music services — two or more is usually one too many.
_STREAMING_KEYWORDS = (
    "netflix", "hulu", "spotify", "apple music", "apple tv", "disney", "hbo",
    "max", "paramount", "peacock", "youtube premium", "prime video", "audible",
    "pandora", "tidal",
)

# A discretionary category is "creeping" only when it has a REAL prior baseline
# and climbed materially above it — otherwise a one-off spike in a near-zero
# category (a vacation, a home project) reads as a fake "6000% increase".
_CREEP_RATIO = 1.3  # a moderate rise (>=30%) is creep…
_CREEP_MAX_RATIO = 2.0  # …but >2x is a one-off spike (a trip, a project), not a habit
_CREEP_BASELINE_MIN = 10_000  # prior 3-mo avg must be >= $100/mo to have a baseline
_CREEP_MIN_JUMP = 10_000  # and the absolute increase must be >= $100/mo
_CREEP_MAX_FLAGS = 3  # surface only the few biggest, not a wall of noise


def classify_category(name: str) -> str:
    lowered = name.lower()
    return "essential" if any(k in lowered for k in _ESSENTIAL_KEYWORDS) else "discretionary"


@dataclass(frozen=True, slots=True)
class CategorySpend:
    name: str
    monthly_avg: Money


@dataclass(frozen=True, slots=True)
class Subscription:
    merchant: str
    amount: Money
    cadence: str


@dataclass(frozen=True, slots=True)
class OneOffSpend:
    name: str
    total: Money  # spent once over the window, not a monthly habit


@dataclass(frozen=True, slots=True)
class SavingsReport:
    currency: str
    months: int
    essential_monthly: Money
    discretionary_monthly: Money
    recurring_ranked: list[CategorySpend]  # monthly habits — where trims stick
    one_off: list[OneOffSpend]  # already-spent one-time purchases
    subscriptions: list[Subscription]
    possible_waste: list[str]
    valued_activities: list[str]
    goals: list[tuple[str, Money]]  # (name, gap to target)


# A discretionary category is a ONE-OFF (already-spent, not trimmable) when its
# spend lands mostly in a single month; a recurring habit is spread across months.
_ONEOFF_CONCENTRATION = 0.7  # one month holds > 70% of the window's total → one-off
_MONTH_ACTIVE_MIN = 1_000  # a month "counts" above $10


def _complete_months(today: date) -> list[tuple[date, date]]:
    """The 3 complete calendar months before the current partial one."""
    this_start = today.replace(day=1)
    months = []
    for k in (3, 2, 1):
        start = finance_service.add_months(this_start, -k)
        end = finance_service.add_months(this_start, -k + 1) - timedelta(days=1)
        months.append((start, end))
    return months


def find_savings(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> SavingsReport:
    today = today or date.today()

    names = {c.id: c.name for c in repository.list_categories(engine, household_id)}
    per_month = [
        repository.sum_spending_by_category(engine, household_id, start, end, currency)
        for start, end in _complete_months(today)
    ]
    category_ids = {cid for month in per_month for cid in month}

    essential_total = 0
    discretionary_total = 0
    recurring: list[CategorySpend] = []
    one_off: list[OneOffSpend] = []
    for category_id in category_ids:
        amounts = [month.get(category_id, 0) for month in per_month]
        total = sum(amounts)
        if total <= 0:
            continue
        name = names.get(category_id, "Other")
        if classify_category(name) == "essential":
            essential_total += total
            continue
        discretionary_total += total
        active = sum(1 for a in amounts if a >= _MONTH_ACTIVE_MIN)
        concentrated = max(amounts) > total * _ONEOFF_CONCENTRATION
        if active >= 2 and not concentrated:
            recurring.append(CategorySpend(name, Money(round(total / 3), currency)))
        else:
            one_off.append(OneOffSpend(name, Money(total, currency)))
    recurring.sort(key=lambda c: c.monthly_avg.amount_minor, reverse=True)
    one_off.sort(key=lambda o: o.total.amount_minor, reverse=True)

    valued = [m.value for m in repository.list_study_insights(engine, household_id)]
    subscriptions = _detect_subscriptions(engine, household_id, currency, today)
    possible_waste = _possible_waste(
        engine, household_id, currency, subscriptions, names, valued, today
    )

    goals: list[tuple[str, Money]] = []
    for goal in repository.list_goals(engine, household_id):
        if goal.currency != currency:
            continue
        gap = max(0, goal.target_minor - goal.current_minor)
        if gap > 0:
            goals.append((goal.name, Money(gap, currency)))

    return SavingsReport(
        currency=currency,
        months=3,
        essential_monthly=Money(round(essential_total / 3), currency),
        discretionary_monthly=Money(round(discretionary_total / 3), currency),
        recurring_ranked=recurring,
        one_off=one_off,
        subscriptions=subscriptions,
        possible_waste=possible_waste,
        valued_activities=valued,
        goals=goals,
    )


def _detect_subscriptions(
    engine: Engine, household_id: str, currency: str, today: date
) -> list[Subscription]:
    """Recurring charges the family might have forgotten. Reuses bill detection
    but keeps the SMALL recurring ones — the subscription-sized leakage, not the
    mortgage."""
    since = today - timedelta(days=bill_detection.LOOKBACK_DAYS)
    rows = repository.list_bill_detection_transactions(engine, household_id, since=since)
    candidates = bill_detection.detect_bill_candidates(
        [
            bill_detection.DetectionTransaction(
                occurred_at=occurred_at,
                amount_minor=amount_minor,
                currency=row_currency,
                merchant=merchant,
                description=description,
            )
            for occurred_at, amount_minor, row_currency, merchant, description in rows
        ]
    )
    # Exclude debt/lease payments and modeled bills — a student-loan or mortgage
    # payment is a recurring charge but NOT a subscription you can cancel.
    obligations = {
        bill_detection.normalize_merchant(a.name)
        for a in repository.list_liability_accounts(engine, household_id)
    } | {
        bill_detection.normalize_merchant(b.name)
        for b in repository.list_bills(engine, household_id)
    }
    obligations.discard("")

    def is_obligation(name: str) -> bool:
        # Substring both ways: "department of education" matches the account
        # "U.S. Department of Education" even though the strings aren't identical.
        norm = bill_detection.normalize_merchant(name)
        return bool(norm) and any(norm in o or o in norm for o in obligations)

    subs = [
        Subscription(c.name, Money(c.amount_minor, c.currency), c.frequency)
        for c in candidates
        if c.currency == currency
        and c.amount_minor <= 10_000  # <= $100: subscription-sized
        and not is_obligation(c.name)
    ]
    subs.sort(key=lambda s: s.amount.amount_minor, reverse=True)
    return subs


def _is_valued(name: str, valued: list[str]) -> bool:
    """True when a category matches something the household clearly enjoys
    (from the study insights) — those are protected, not flagged as waste."""
    lowered = name.lower()
    return any(lowered in v.lower() for v in valued)


def _possible_waste(
    engine: Engine,
    household_id: str,
    currency: str,
    subscriptions: list[Subscription],
    names: dict[str, str],
    valued: list[str],
    today: date,
) -> list[str]:
    flags: list[str] = []

    # Duplicate streaming/music services.
    streaming = [
        s.merchant
        for s in subscriptions
        if any(k in s.merchant.lower() for k in _STREAMING_KEYWORDS)
    ]
    if len(streaming) >= 2:
        flags.append(
            "Multiple streaming/music subscriptions ("
            + ", ".join(streaming)
            + ") — likely one or two you could drop."
        )

    # Category creep: latest complete month vs the prior trailing average.
    last_start = finance_service.add_months(today.replace(day=1), -1)
    last_end = today.replace(day=1) - timedelta(days=1)
    prior_start = finance_service.add_months(last_start, -3)
    prior_end = last_start - timedelta(days=1)
    last_month = repository.sum_spending_by_category(engine, household_id, last_start, last_end, currency)
    prior = repository.sum_spending_by_category(engine, household_id, prior_start, prior_end, currency)
    creeps: list[tuple[int, str]] = []  # (absolute jump, message)
    for category_id, last_minor in last_month.items():
        name = names.get(category_id, "Other")
        # Skip needs, and never flag an activity the family values as "waste".
        if classify_category(name) != "discretionary" or _is_valued(name, valued):
            continue
        prior_avg = prior.get(category_id, 0) / 3
        jump = last_minor - prior_avg
        # A real baseline AND a material, proportional increase — not a one-off
        # spike in a category that was near zero before.
        if (
            prior_avg >= _CREEP_BASELINE_MIN
            and jump >= _CREEP_MIN_JUMP
            and _CREEP_RATIO < last_minor / prior_avg <= _CREEP_MAX_RATIO
        ):
            pct = round((last_minor / prior_avg - 1) * 100)
            creeps.append((round(jump), f"{name} last month was {pct}% above its recent average."))
    creeps.sort(reverse=True)
    flags.extend(msg for _, msg in creeps[:_CREEP_MAX_FLAGS])
    return flags
