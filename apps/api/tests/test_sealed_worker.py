"""#115: the API runs a sealed household's unattended work, the worker does not.

The bug this covers is structural rather than logical, so the tests are mostly
about WHICH process owns WHICH household. A sealed household's key lives only
in the API process's session keyring; the worker is a separate container and
can never see it, so before this the work simply never ran — the ADR's promise
that it "drains during the next active session" had no implementation behind it.
"""

import threading

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import update as sql_update

from family_cfo_api import (
    household_crypto,
    models,
    net_worth_history,
    repository,
    sealed_worker,
)
from family_cfo_api.config import Settings, get_settings


@pytest.fixture
def _master_key(monkeypatch):
    monkeypatch.setenv("FAMILY_CFO_MASTER_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    yield
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    household_crypto.set_unlock_listener(None)


def _seal(engine, household_id: str) -> None:
    """Flip the flag directly. `seal_household` needs a member wrap and a
    recovery key; the ownership split only reads the flag, so the preconditions
    are a different test's business."""
    with engine.begin() as conn:
        conn.execute(
            sql_update(models.households)
            .where(models.households.c.id == household_id)
            .values(sealed_mode=True)
        )


def _settings() -> Settings:
    return Settings(health_check_database=False)


# --- who owns which household -------------------------------------------------


def test_nothing_is_sealed_when_encryption_is_off(demo_engine, monkeypatch) -> None:
    monkeypatch.delenv("FAMILY_CFO_MASTER_KEY", raising=False)
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    # The flag is set, but with no master key there is no sealing to speak of —
    # everything is the worker's, which is what the pre-encryption box did.
    assert household_crypto.sealed_household_ids(demo_engine) == set()
    assert household_crypto.unlocked_sealed_household_ids(demo_engine) == set()


def test_a_sealed_household_is_not_the_workers(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    assert household_crypto.sealed_household_ids(demo_engine) == {hh}


def test_sealed_but_locked_is_nobodys_work(_master_key, demo_engine) -> None:
    """The worker skips it because it is sealed; the API skips it because no
    session holds the key. That gap is the honest cost of sealed mode."""
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    household_crypto.reset_cache_for_tests()
    assert household_crypto.unlocked_sealed_household_ids(demo_engine) == set()


def test_sealed_and_unlocked_here_is_this_process_to_run(_master_key, demo_engine) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    assert household_crypto.unlocked_sealed_household_ids(demo_engine) == {hh}


def test_an_unsealed_household_is_never_the_apis(_master_key, demo_engine) -> None:
    """Even with a key in the keyring: a convenient household has a box wrap, so
    the worker runs it. Both processes doing it would double every job."""
    hh = repository.list_households(demo_engine)[0]
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    assert household_crypto.unlocked_sealed_household_ids(demo_engine) == set()


# --- the unlock hook ----------------------------------------------------------


def test_unlocking_announces_the_household(_master_key, demo_engine) -> None:
    seen: list[str] = []
    household_crypto.set_unlock_listener(seen.append)
    hh = repository.list_households(demo_engine)[0]
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    assert seen == [hh]


def test_clearing_the_listener_stops_the_announcements(_master_key, demo_engine) -> None:
    seen: list[str] = []
    household_crypto.set_unlock_listener(seen.append)
    household_crypto.set_unlock_listener(None)
    hh = repository.list_households(demo_engine)[0]
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    assert seen == []


def test_a_failing_listener_never_breaks_the_unlock(_master_key, demo_engine) -> None:
    """Signing in has to succeed even if the follow-on work cannot be started."""

    def explode(_household_id: str) -> None:
        raise RuntimeError("the follow-on work could not start")

    household_crypto.set_unlock_listener(explode)
    hh = repository.list_households(demo_engine)[0]
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    # The key is in the keyring regardless of the listener blowing up.
    assert household_crypto.unlocked_sealed_household_ids(demo_engine) == set()
    assert household_crypto._keyring_get(demo_engine, hh) is not None


# --- the pass itself ----------------------------------------------------------


def _stub_jobs(monkeypatch) -> dict[str, list]:
    """Replace the real jobs with recorders: this is about dispatch, not about
    re-testing six subsystems."""
    calls: dict[str, list] = {name: [] for name in ("imports", "snapshot", "sync", "reports", "index", "study")}
    monkeypatch.setattr(
        sealed_worker.import_processing,
        "run_pending_imports_once",
        lambda engine, staging, households=None: calls["imports"].append(households),
    )
    monkeypatch.setattr(
        sealed_worker.net_worth_history,
        "record_snapshot_once",
        lambda engine, households=None: calls["snapshot"].append(households),
    )
    monkeypatch.setattr(
        sealed_worker.banksync,
        "sync_due_connections",
        lambda engine, settings, households=None: (calls["sync"].append(households), set())[1],
    )
    monkeypatch.setattr(
        sealed_worker.report_generation,
        "run_scheduled_reports_once",
        lambda engine, report_type, households=None: calls["reports"].append(report_type),
    )
    monkeypatch.setattr(
        sealed_worker.vector_indexing,
        "run_indexing_once",
        lambda engine, settings, wipe=False, households=None: calls["index"].append(
            (wipe, households)
        ),
    )
    monkeypatch.setattr(
        sealed_worker.ai_study,
        "run_study_tick",
        lambda engine, settings, households=None: calls["study"].append(households),
    )
    return calls


def test_a_pass_runs_every_job_for_the_unlocked_sealed_household(
    _master_key, demo_engine, monkeypatch
) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    calls = _stub_jobs(monkeypatch)

    assert sealed_worker.run_due_work_once(demo_engine, _settings()) == {hh}

    assert calls["imports"] == [{hh}]
    assert calls["snapshot"] == [{hh}]
    assert calls["sync"] == [{hh}]
    assert calls["reports"] == ["weekly", "monthly", "annual"]
    assert calls["study"] == [{hh}]
    # wipe=True is safe from here because the wipe is household-scoped (#115
    # review): only the covered sealed households are cleared and rebuilt, so
    # they get the same deleted-row pruning convenient households do.
    assert calls["index"] == [(True, {hh})]


def test_no_pass_runs_when_no_sealed_household_is_open(
    _master_key, demo_engine, monkeypatch
) -> None:
    calls = _stub_jobs(monkeypatch)
    assert sealed_worker.run_due_work_once(demo_engine, _settings()) == set()
    assert all(recorded == [] for recorded in calls.values())


def test_one_failing_job_does_not_abandon_the_rest(_master_key, demo_engine, monkeypatch) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    calls = _stub_jobs(monkeypatch)

    def boom(engine, staging, households=None):
        raise RuntimeError("import subsystem down")

    monkeypatch.setattr(sealed_worker.import_processing, "run_pending_imports_once", boom)

    assert sealed_worker.run_due_work_once(demo_engine, _settings()) == {hh}
    assert calls["study"] == [{hh}]  # the tail of the pass still ran


def test_a_session_expiring_mid_pass_is_not_an_error(
    _master_key, demo_engine, monkeypatch
) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    calls = _stub_jobs(monkeypatch)

    def locked_again(engine, households=None):
        raise household_crypto.HouseholdLockedError(hh)

    monkeypatch.setattr(sealed_worker.net_worth_history, "record_snapshot_once", locked_again)

    assert sealed_worker.run_due_work_once(demo_engine, _settings()) == {hh}
    assert calls["study"] == [{hh}]


def test_only_one_pass_runs_at_a_time(_master_key, demo_engine, monkeypatch) -> None:
    """An unlock fires a pass immediately while the periodic tick keeps
    running; without the guard a sign-in mid-tick would start a second pass
    over the same households."""
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    _stub_jobs(monkeypatch)

    started = threading.Event()
    release = threading.Event()
    second: list[set] = []

    def slow_first(engine, staging, households=None):
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(sealed_worker.import_processing, "run_pending_imports_once", slow_first)

    first = threading.Thread(target=lambda: sealed_worker.run_due_work_once(demo_engine, _settings()))
    first.start()
    assert started.wait(timeout=5)
    second.append(sealed_worker.run_due_work_once(demo_engine, _settings()))
    release.set()
    first.join(timeout=5)

    assert second == [set()]  # the overlapping pass declined rather than doubling up


def test_background_pass_targets_just_that_household(
    _master_key, demo_engine, monkeypatch
) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    calls = _stub_jobs(monkeypatch)

    # No keyring entry: an explicit household set is honoured as given, which is
    # what the unlock hook passes before the caller's key is visible elsewhere.
    sealed_worker.run_due_work_in_background(demo_engine, _settings(), hh).join(timeout=5)

    assert calls["snapshot"] == [{hh}]


# --- the filter the jobs grew -------------------------------------------------


def test_a_job_honours_the_household_filter(demo_engine, monkeypatch) -> None:
    monkeypatch.delenv("FAMILY_CFO_MASTER_KEY", raising=False)
    get_settings.cache_clear()
    household_crypto.reset_cache_for_tests()
    households = repository.list_households(demo_engine)
    captured = net_worth_history.record_snapshot_once(demo_engine, households=set())
    assert captured == 0, "an empty filter must mean 'none of them', not 'all of them'"
    assert net_worth_history.record_snapshot_once(demo_engine, households=households[:1]) == 1


# The old guard here ("a filtered pass must not wipe") is gone with the global
# wipe itself: pruning is household-scoped now, so filter+wipe is the normal,
# safe combination. test_vector_retrieval.py::test_wipe_is_household_scoped
# pins the property that replaced it.


# --- the TTL is member activity, not polling (#115 review) --------------------


def _frozen_clock(monkeypatch):
    """Controllable time, plus a pinned key generation. The 300s jumps here put
    every read past the revalidation window, and these households have no DEK
    row for the real generation query to agree with — that check has its own
    tests; these are about the deadline."""
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(household_crypto, "_now", lambda: clock["t"])
    monkeypatch.setattr(household_crypto, "_current_generation", lambda engine, hh: 7)
    return clock


def test_background_polling_never_keeps_a_session_alive(
    _master_key, demo_engine, monkeypatch
) -> None:
    """The review scenario: one sign-in, member walks away, the five-minute
    tick polls forever. Before the fix the key was still alive four hours
    later; the sliding TTL treated every read as activity."""
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    clock = _frozen_clock(monkeypatch)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())

    ticks = 0
    while household_crypto.unlocked_sealed_household_ids(demo_engine):
        clock["t"] += 300  # the scheduler's interval
        ticks += 1
        assert ticks < 50, "the key outlived its TTL under background polling"

    # Dead at exactly the TTL the sign-in granted, ticks notwithstanding.
    assert ticks == household_crypto.SESSION_KEYRING_TTL_SECONDS // 300 + 1


def test_a_member_read_still_slides_the_deadline(_master_key, demo_engine, monkeypatch) -> None:
    """Foreground reads are the activity the sliding TTL exists for."""
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    clock = _frozen_clock(monkeypatch)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())

    # Read a minute before each expiry, twice: alive well past one bare TTL.
    for _ in range(2):
        clock["t"] += household_crypto.SESSION_KEYRING_TTL_SECONDS - 60
        assert household_crypto._keyring_get(demo_engine, hh) is not None
    # Then walk away for real.
    clock["t"] += household_crypto.SESSION_KEYRING_TTL_SECONDS + 1
    assert household_crypto._keyring_get(demo_engine, hh) is None


def test_a_passive_read_leaves_the_deadline_where_it_was(
    _master_key, demo_engine, monkeypatch
) -> None:
    hh = repository.list_households(demo_engine)[0]
    clock = _frozen_clock(monkeypatch)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    deadline = household_crypto._session_keyring[hh].expires

    clock["t"] += 300
    with household_crypto.without_extending_sessions():
        assert household_crypto._keyring_get(demo_engine, hh) is not None
    assert household_crypto._session_keyring[hh].expires == deadline

    # The same read outside the context slides it — the contrast that matters.
    assert household_crypto._keyring_get(demo_engine, hh) is not None
    assert household_crypto._session_keyring[hh].expires > deadline


def test_a_passive_revalidation_records_the_check_but_not_activity(
    _master_key, demo_engine, monkeypatch
) -> None:
    """Past the revalidation window a passive read still re-checks the key
    generation against the database; recording that must not move the
    deadline."""
    hh = repository.list_households(demo_engine)[0]
    clock = _frozen_clock(monkeypatch)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    deadline = household_crypto._session_keyring[hh].expires
    checked = household_crypto._session_keyring[hh].checked_at

    clock["t"] += household_crypto.DEK_CACHE_REVALIDATE_SECONDS + 1
    with household_crypto.without_extending_sessions():
        assert household_crypto._keyring_get(demo_engine, hh) is not None

    entry = household_crypto._session_keyring[hh]
    assert entry.checked_at > checked, "the generation check was recorded"
    assert entry.expires == deadline, "the deadline did not move"


def test_nested_passive_contexts_restore_the_outer_flag(
    _master_key, demo_engine, monkeypatch
) -> None:
    """An inner context exiting must not switch extension back on for the rest
    of an enclosing background pass — discovery nests inside the pass."""
    hh = repository.list_households(demo_engine)[0]
    clock = _frozen_clock(monkeypatch)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    deadline = household_crypto._session_keyring[hh].expires

    with household_crypto.without_extending_sessions():
        with household_crypto.without_extending_sessions():
            pass
        clock["t"] += 300
        assert household_crypto._keyring_get(demo_engine, hh) is not None
    assert household_crypto._session_keyring[hh].expires == deadline
