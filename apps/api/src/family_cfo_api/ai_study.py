"""Idle-time study of the household's transaction history (ADR 0040).

The worker walks COMPLETE calendar months (newest first), builds a
deterministic digest of each month straight from Postgres, and asks the
household's selected runtime to distill durable insights into household
memories (``source="study"``). Insight keys are stable, so re-studying a month
UPDATES the advisor's knowledge instead of piling up near-duplicates — the
injected context stays bounded no matter how long the box studies.

Knowledge lives in memories and rows, never in model weights: it stays
current, exact, auditable, and deletable — the reasons ADR 0040 rejected
fine-tuning. Everything here is best-effort: a failed study pass logs and
waits for the next tick; it never takes anything else down.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.engine import Engine

from family_cfo_ai_orchestrator import RuntimeMessage, RuntimeUnavailableError

from family_cfo_api import repository
from family_cfo_api.ai_memory import parse_extracted_memories
from family_cfo_api.ai_runtime_selection import resolve_ai_config, select_tool_runtime
from family_cfo_api.config import Settings

logger = logging.getLogger(__name__)

# A chat this recent means a human is (or was just) using the runtime — the
# study job yields rather than compete for the GPU.
STUDY_QUIET_MINUTES = 10

MAX_INSIGHTS_PER_MONTH = 5

_STUDY_SYSTEM_PROMPT = (
    "You are studying one month of a family's finances to build durable "
    "knowledge their financial advisor will rely on in every future "
    "conversation. You receive a factual digest computed from their database. "
    'Return ONLY a JSON array (no prose, no code fences) of {"key": '
    'snake_case identifier, "value": one short self-contained sentence}. '
    "Keys must be STABLE across months so a fresher month overwrites the old "
    "insight instead of duplicating it (e.g. grocery_spending_pattern, "
    "income_rhythm, largest_recurring_costs, seasonal_spending) — never put "
    "the month or year in a key. Values state the pattern with its concrete "
    "figures and, when it matters, the month observed. Extract only patterns "
    "useful for advice: typical spend per area, income cadence, unusually "
    "large one-offs worth remembering, category trends. Do NOT restate raw "
    "numbers the database already answers directly (exact balances, single "
    "transactions). Return [] when the month teaches nothing new."
)


def month_bounds(month: str) -> tuple[date, date]:
    """[first day, last day] of a YYYY-MM month."""
    start = date(int(month[:4]), int(month[5:7]), 1)
    next_start = date(start.year + 1, 1, 1) if start.month == 12 else start.replace(month=start.month + 1)
    return start, next_start - timedelta(days=1)


def complete_months(engine: Engine, household_id: str, *, today: date | None = None) -> list[str]:
    """Every complete calendar month from the first transaction through last
    month, oldest first. The current partial month is never listed — it would
    go stale daily and make coverage a moving target."""
    earliest = repository.earliest_transaction_month(engine, household_id)
    if earliest is None:
        return []
    today = today or date.today()
    cursor = date(int(earliest[:4]), int(earliest[5:7]), 1)
    end = today.replace(day=1)  # exclusive: the current month
    months: list[str] = []
    while cursor < end:
        months.append(f"{cursor.year}-{cursor.month:02d}")
        cursor = (
            date(cursor.year + 1, 1, 1) if cursor.month == 12 else cursor.replace(month=cursor.month + 1)
        )
    return months


def build_month_digest(engine: Engine, household_id: str, currency: str, month: str) -> dict:
    """The deterministic facts of one month, straight from Postgres."""
    start, end = month_bounds(month)
    category_names = {c.id: c.name for c in repository.list_categories(engine, household_id)}
    by_category = repository.sum_spending_by_category(engine, household_id, start, end, currency)
    return {
        "month": month,
        "currency": currency,
        "income_minor": repository.sum_income(engine, household_id, start, end, currency),
        "spending_minor": repository.sum_spending(engine, household_id, start, end, currency),
        "spending_by_category": {
            category_names.get(category_id, "Other"): amount
            for category_id, amount in sorted(by_category.items(), key=lambda kv: -kv[1])
        },
        "top_merchants": {
            m.merchant: m.amount_minor
            for m in repository.top_spending_merchants(engine, household_id, start, end, currency, limit=8)
        },
    }


def digest_fingerprint(digest: dict) -> str:
    """Stable hash of a month's data; a mismatch means the month changed
    (recategorization, late import) and deserves re-study."""
    return hashlib.sha256(json.dumps(digest, sort_keys=True).encode()).hexdigest()


def _format_digest(digest: dict) -> str:
    def money(minor: int) -> str:
        return f"{minor / 100:,.2f} {digest['currency']}"

    lines = [
        f"Month: {digest['month']}",
        f"Income received: {money(digest['income_minor'])}",
        f"Total spending: {money(digest['spending_minor'])}",
        "Spending by category:",
        *(f"  - {name}: {money(amount)}" for name, amount in digest["spending_by_category"].items()),
        "Top merchants by spend:",
        *(f"  - {name}: {money(amount)}" for name, amount in digest["top_merchants"].items()),
    ]
    return "\n".join(lines)


def study_month(
    runtime, engine: Engine, household_id: str, currency: str, month: str, *, model: str | None = None
) -> int:
    """One study pass: digest → runtime → upserted insights. Returns how many."""
    digest = build_month_digest(engine, household_id, currency, month)
    completion = runtime.complete(
        [
            RuntimeMessage(role="system", content=_STUDY_SYSTEM_PROMPT),
            RuntimeMessage(role="user", content=_format_digest(digest)),
        ],
        temperature=0.0,
        max_tokens=600,
    )
    pairs = parse_extracted_memories(completion.text)[:MAX_INSIGHTS_PER_MONTH]
    for key, value in pairs:
        repository.upsert_household_memory(engine, household_id, key, value, source="study")
    repository.upsert_study_month(
        engine,
        household_id,
        month,
        digest_hash=digest_fingerprint(digest),
        insight_count=len(pairs),
        model=model,
    )
    return len(pairs)


def _next_month_to_study(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> str | None:
    """Newest unstudied complete month first (fresh months help advice most),
    then the newest studied month whose data changed since its last pass."""
    months = complete_months(engine, household_id, today=today)
    if not months:
        return None
    studied = {m.month: m.digest_hash for m in repository.list_study_months(engine, household_id)}
    for month in reversed(months):
        if month not in studied:
            return month
    for month in reversed(months):
        digest = build_month_digest(engine, household_id, currency, month)
        if digest_fingerprint(digest) != studied[month]:
            return month
    return None


def run_study_tick(engine: Engine, settings: Settings | None = None) -> None:
    """One scheduler tick: study at most ONE month per household, and only
    while the advisor is idle. Never raises."""
    for household_id in repository.list_households(engine):
        try:
            config = resolve_ai_config(engine, household_id, settings)
            if not config.is_usable:
                continue
            last_chat = repository.last_chat_message_at(engine, household_id)
            if last_chat is not None:
                idle_for = datetime.now(tz=last_chat.tzinfo) - last_chat
                if idle_for < timedelta(minutes=STUDY_QUIET_MINUTES):
                    continue
            currency = repository.get_household(engine, household_id).base_currency
            month = _next_month_to_study(engine, household_id, currency)
            if month is None:
                continue
            runtime = select_tool_runtime(engine, household_id, settings)
            if runtime is None:
                continue
            try:
                count = study_month(
                    runtime, engine, household_id, currency, month, model=config.model
                )
                logger.info(
                    "studied month household_id=%s month=%s insights=%d",
                    household_id,
                    month,
                    count,
                )
            finally:
                runtime.close()
        except RuntimeUnavailableError:
            logger.info("study skipped: runtime unavailable household_id=%s", household_id)
        except Exception:  # noqa: BLE001 — background study must never break the worker
            logger.exception("study tick failed household_id=%s", household_id)


@dataclass(frozen=True, slots=True)
class StudyStatus:
    total_months: int
    studied_months: int
    stale_months: int
    last_studied_at: datetime | None
    runtime_usable: bool
    insights: list[repository.HouseholdMemoryRecord]


def study_status(
    engine: Engine,
    household_id: str,
    settings: Settings | None = None,
    *,
    today: date | None = None,
) -> StudyStatus:
    months = set(complete_months(engine, household_id, today=today))
    rows = [m for m in repository.list_study_months(engine, household_id) if m.month in months]
    config = resolve_ai_config(engine, household_id, settings)
    return StudyStatus(
        total_months=len(months),
        studied_months=len(rows),
        # Staleness by hash requires recomputing digests; the status endpoint
        # stays cheap and reports 0 — the worker's tick is where staleness is
        # detected and repaired.
        stale_months=0,
        last_studied_at=max((m.studied_at for m in rows), default=None),
        runtime_usable=config.is_usable,
        insights=repository.list_study_insights(engine, household_id),
    )
