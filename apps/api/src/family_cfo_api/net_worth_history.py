"""M40: periodic net-worth snapshots for the Overview trend.

Run by the worker daily (and once at startup so history begins immediately).
Idempotent per day — re-running overwrites today's row rather than appending.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.engine import Engine

from family_cfo_api import finance_service, household_crypto, repository

logger = logging.getLogger(__name__)


def record_snapshot_once(engine: Engine, *, today: date | None = None) -> int:
    """Capture today's net worth for every household. Returns the count captured."""
    today = today or date.today()
    captured = 0
    for household_id in repository.list_households(engine):
        try:
            household = repository.get_household(engine, household_id)
            if household is None:
                continue
            currency = household.base_currency
            result = finance_service.compute_net_worth(engine, household_id, currency)
            net_worth = result.outputs["net_worth"]
            repository.record_net_worth_snapshot(
                engine, household_id, today, net_worth.amount_minor, currency
            )
            captured += 1
        except household_crypto.HouseholdLockedError:
            # #181: one sealed+locked household must never stall the others.
            logger.info("net-worth snapshot deferred: household %s locked", household_id)
    logger.info("net-worth snapshot captured for %s household(s)", captured)
    return captured
