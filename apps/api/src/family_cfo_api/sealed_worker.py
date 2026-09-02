"""Unattended work for SEALED households, run by the API (#115, ADR 0072).

A sealed household has no box wrap on purpose, so its key exists only in the
session keyring — a dict in this process's memory, opened when a member signs
in, a paired device posts its unwrapped key, or the recovery key is used. The
worker is a separate container with its own address space and can never see
that, which is why ADR 0072 Phase 3's "queued background work drains during the
next active session" was never true: the unlock happened here and the jobs ran
there.

So the work moves to where the key is. This module runs the same job functions
the worker runs, restricted to the sealed households THIS process currently
holds keys for. The worker keeps every household it can open on its own — the
two sets are disjoint by construction, so nothing is done twice and nothing is
dropped:

    worker  ->  households NOT sealed          (box wrap opens them)
    API     ->  sealed households unlocked here (session keyring opens them)

Deliberately NOT named a "drain", despite the ADR's phrase. A drain empties a
queue that filled while you were away; there is no queue here and nothing
accumulates. These are the worker's own pollers, relocated: each looks at
current state and runs what is due NOW. Cadence-gated jobs self-heal — a report
skips periods it already wrote, a bank sync is due or it is not — but a
net-worth snapshot is stamped for the day it runs, so days when nobody signed
in leave gaps in a sealed household's trend. Sealed mode trades unattended
completeness for the box holding no usable key at rest; this closes the gap to
"runs whenever someone is here", not to "runs like convenient mode".
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Collection

from sqlalchemy.engine import Engine

from family_cfo_api import (
    ai_study,
    banksync,
    finance_service,
    household_crypto,
    import_processing,
    net_worth_history,
    report_generation,
    vector_indexing,
)
from family_cfo_api.config import Settings

logger = logging.getLogger(__name__)

# The heavy jobs here are all internally cadence-gated (a sync is due once a
# day, a report skips a period it already wrote, study yields while the advisor
# is busy), so the tick only has to be frequent enough that a signed-in member
# gets their work done inside a session — not frequent enough to drive it.
WORK_INTERVAL_SECONDS = 300

# One pass at a time. An unlock fires a pass immediately, and the periodic
# tick keeps running; without this a member signing in mid-tick would start a
# second pass over the same households.
_work_lock = threading.Lock()


def _guarded(name: str, run: Callable[[], object]) -> None:
    """One job must not take the rest of the pass down with it. The worker gets
    this from its scheduler; here the jobs run in one pass, so it is explicit."""
    try:
        run()
    except household_crypto.HouseholdLockedError:
        # The session expired mid-pass (30-minute sliding TTL) or the key was
        # retired by a rotation. Not an error: the next unlock runs it again.
        logger.info("sealed-household work: %s stopped, household locked again", name)
    except Exception:
        logger.exception("sealed-household work: %s failed", name)


def run_due_work_once(
    engine: Engine, settings: Settings, *, households: Collection[str] | None = None
) -> set[str]:
    """Run every unattended job that is due for the sealed households this
    process can open.

    Returns the households covered, so callers (and tests) can assert on the
    set rather than on log output. Never raises.
    """
    targets = (
        set(households)
        if households is not None
        else household_crypto.unlocked_sealed_household_ids(engine)
    )
    if not targets:
        return set()

    if not _work_lock.acquire(blocking=False):
        logger.debug("sealed-household work: a pass is already running, skipping")
        return set()
    try:
        logger.info("sealed-household work: starting for %d household(s)", len(targets))

        _guarded(
            "imports",
            lambda: import_processing.run_pending_imports_once(
                engine, settings.import_staging_dir, households=targets
            ),
        )
        _guarded(
            "net-worth snapshot",
            lambda: net_worth_history.record_snapshot_once(engine, households=targets),
        )

        def sync_and_autofile() -> None:
            synced = banksync.sync_due_connections(engine, settings, households=targets)
            # M96: auto-file what the sync just imported, exactly as the worker
            # does, so a sealed household's Categorize queue is not left full.
            for household_id in synced:
                try:
                    finance_service.autofile_all(engine, household_id)
                except household_crypto.HouseholdLockedError:
                    continue  # #181: per-household isolation

        _guarded("bank sync", sync_and_autofile)

        for report_type in ("weekly", "monthly", "annual"):
            _guarded(
                f"{report_type} reports",
                lambda report_type=report_type: report_generation.run_scheduled_reports_once(
                    engine, report_type, households=targets
                ),
            )

        # Additive, never a wipe: the wipe clears the whole collection and this
        # pass only covers sealed households. It also repairs what the worker's
        # nightly wipe drops, since the worker cannot re-index what it cannot
        # decrypt.
        _guarded(
            "vector index",
            lambda: vector_indexing.run_indexing_once(
                engine, settings, wipe=False, households=targets
            ),
        )
        _guarded(
            "advisor study",
            lambda: ai_study.run_study_tick(engine, settings, households=targets),
        )

        logger.info("sealed-household work: finished for %d household(s)", len(targets))
        return targets
    finally:
        _work_lock.release()


def run_due_work_in_background(
    engine: Engine, settings: Settings, household_id: str
) -> threading.Thread:
    """Run one household's due work without making the caller wait — used by
    the unlock listener so "signing in starts the work" is literal rather than
    "within five minutes". Daemon, so it never holds up a shutdown."""

    def run() -> None:
        run_due_work_once(engine, settings, households={household_id})

    thread = threading.Thread(
        target=run, name=f"sealed-work-{household_id[:8]}", daemon=True
    )
    thread.start()
    return thread
