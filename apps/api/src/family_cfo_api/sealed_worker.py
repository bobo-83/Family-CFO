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
import time
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

# Discovery is the one unguarded database step in an unlock-triggered pass. A
# transient database failure must retain the one-shot notification, but retrying
# it in a tight loop would amplify an outage. Back off to a bounded cadence.
UNLOCK_RETRY_INITIAL_SECONDS = 1.0
UNLOCK_RETRY_MAX_SECONDS = 30.0

# One pass at a time. An unlock fires a pass immediately, and the periodic
# tick keeps running; without this a member signing in mid-tick would start a
# second pass over the same households.
_work_lock = threading.Lock()

# Unlocks can arrive while a long pass owns `_work_lock`. The periodic caller
# may skip that overlap, but an unlock is a one-shot notification: retain and
# coalesce those household ids until a single runner can take the work lock.
_pending_lock = threading.Lock()
_pending_households: set[str] = set()
_pending_thread: threading.Thread | None = None


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

    The whole pass — discovery and jobs — runs under
    `without_extending_sessions()`: these reads must not count as member
    activity, or this very scheduler becomes the "activity" that keeps a
    sealed household's key alive forever after one sign-in. Expiry and the
    generation re-check still apply; a session that ends mid-pass ends the
    pass for that household.
    """
    with household_crypto.without_extending_sessions():
        return _run_due_work_once_passive(engine, settings, households, wait_for_lock=False)


def _run_due_work_once_passive(
    engine: Engine,
    settings: Settings,
    households: Collection[str] | None,
    *,
    wait_for_lock: bool,
) -> set[str]:
    if not _work_lock.acquire(blocking=wait_for_lock):
        logger.debug("sealed-household work: a pass is already running, skipping")
        return set()
    try:
        # Explicit unlock notifications are only hints. `_keyring_put` also
        # announces valid convenient-mode unlocks and a queued notification can
        # outlive its key, so intersect every supplied set with current sealed
        # ownership and current key availability before dispatch.
        available = household_crypto.unlocked_sealed_household_ids(engine)
        targets = available if households is None else set(households) & available
        if not targets:
            return set()

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

        # wipe=True is household-scoped (#115 review): each target household is
        # cleared and rebuilt individually, so sealed households get the same
        # deleted-row pruning convenient ones do, and this pass can never touch
        # a household the worker owns.
        _guarded(
            "vector index",
            lambda: vector_indexing.run_indexing_once(
                engine, settings, wipe=True, households=targets
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
    """Queue an unlock-triggered pass without making the caller wait.

    One daemon drains all pending household ids. Unlike the periodic tick it
    waits behind an active pass, because an unlock notification is not repeated
    and must not be discarded merely because another household is still busy.
    """
    global _pending_thread

    def run_pending() -> None:
        global _pending_thread

        retry_delay = UNLOCK_RETRY_INITIAL_SECONDS
        while True:
            with _pending_lock:
                if not _pending_households:
                    # Clear while holding the same lock producers use. An unlock
                    # arriving after this point starts the next runner itself.
                    _pending_thread = None
                    return
                targets = set(_pending_households)
                _pending_households.clear()

            try:
                with household_crypto.without_extending_sessions():
                    _run_due_work_once_passive(engine, settings, targets, wait_for_lock=True)
            except Exception:
                # Discovery can fail before the per-job guards are reached. The
                # unlock notification is one-shot, so put its targets back and
                # retry with bounded backoff rather than silently losing them.
                logger.exception(
                    "sealed-household work: queued unlock pass failed; retrying in %.1fs",
                    retry_delay,
                )
                with _pending_lock:
                    _pending_households.update(targets)
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, UNLOCK_RETRY_MAX_SECONDS)
            else:
                retry_delay = UNLOCK_RETRY_INITIAL_SECONDS

    with _pending_lock:
        _pending_households.add(household_id)
        if _pending_thread is not None:
            return _pending_thread

        thread = threading.Thread(target=run_pending, name="sealed-work-unlocks", daemon=True)
        _pending_thread = thread
        try:
            thread.start()
        except Exception:
            _pending_thread = None
            raise
        return thread
