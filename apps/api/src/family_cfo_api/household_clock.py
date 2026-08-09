"""#41: what "today" means for a given household.

`date.today()` is the date where the PROCESS runs. The API container runs UTC,
so for a household west of UTC the app believed it was tomorrow from the early
evening onward — bills read due-soon a day early, items flipped to overdue, and
safe-to-spend's horizon slid. Nothing was corrupted; the picture was simply a
day ahead for the last few hours of each evening.

Hosting more than one household makes it structural rather than cosmetic: a box
serving households in different zones cannot have one shared "today" that is
correct for all of them.

Resolution order, most specific first:

  1. the household's own `timezone` column
  2. FAMILY_CFO_DEFAULT_TIMEZONE (the box's own zone, set in compose)
  3. the process's local date — today's behaviour, so nothing shifts for a
     household that has never chosen a zone

An unknown or malformed zone falls back rather than raising: a typo in a
setting must not take the Overview down.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE_ENV = "FAMILY_CFO_DEFAULT_TIMEZONE"


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A bad zone name is a settings problem, not a reason to fail a request.
        logger.warning("unknown timezone %r — falling back", name)
        return None


def resolve_zone(household_timezone: str | None) -> ZoneInfo | None:
    """The zone to reckon dates in, or None to use the process's local time."""
    return _zone(household_timezone) or _zone(os.environ.get(DEFAULT_TIMEZONE_ENV))


def today_for(household_timezone: str | None) -> date:
    """The household's current date."""
    zone = resolve_zone(household_timezone)
    return datetime.now(zone).date() if zone else date.today()


def today_for_household(engine, household_id: str) -> date:
    """Convenience for call sites that hold an engine and an id.

    Kept separate from `today_for` so the pure function stays trivially
    testable without a database.
    """
    from family_cfo_api import repository

    household = repository.get_household(engine, household_id)
    return today_for(household.timezone if household else None)
