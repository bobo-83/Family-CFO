"""#115: the API drains a sealed household's unattended work, the worker does not.

The bug this covers is structural rather than logical, so the tests are mostly
about WHICH process owns WHICH household. A sealed household's key lives only
in the API process's session keyring; the worker is a separate container and
can never see it, so before this the work simply never ran — the ADR's "drains
during the next active session" had no implementation behind it.
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
    sealed_drain,
    vector_indexing,
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
        raise RuntimeError("drain could not start")

    household_crypto.set_unlock_listener(explode)
    hh = repository.list_households(demo_engine)[0]
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    # The key is in the keyring regardless of the listener blowing up.
    assert household_crypto.unlocked_sealed_household_ids(demo_engine) == set()
    assert household_crypto._keyring_get(demo_engine, hh) is not None


# --- the drain itself ---------------------------------------------------------


def _stub_jobs(monkeypatch) -> dict[str, list]:
    """Replace the real jobs with recorders: this is about dispatch, not about
    re-testing six subsystems."""
    calls: dict[str, list] = {name: [] for name in ("imports", "snapshot", "sync", "reports", "index", "study")}
    monkeypatch.setattr(
        sealed_drain.import_processing,
        "run_pending_imports_once",
        lambda engine, staging, households=None: calls["imports"].append(households),
    )
    monkeypatch.setattr(
        sealed_drain.net_worth_history,
        "record_snapshot_once",
        lambda engine, households=None: calls["snapshot"].append(households),
    )
    monkeypatch.setattr(
        sealed_drain.banksync,
        "sync_due_connections",
        lambda engine, settings, households=None: (calls["sync"].append(households), set())[1],
    )
    monkeypatch.setattr(
        sealed_drain.report_generation,
        "run_scheduled_reports_once",
        lambda engine, report_type, households=None: calls["reports"].append(report_type),
    )
    monkeypatch.setattr(
        sealed_drain.vector_indexing,
        "run_indexing_once",
        lambda engine, settings, wipe=False, households=None: calls["index"].append(
            (wipe, households)
        ),
    )
    monkeypatch.setattr(
        sealed_drain.ai_study,
        "run_study_tick",
        lambda engine, settings, households=None: calls["study"].append(households),
    )
    return calls


def test_drain_runs_every_job_for_the_unlocked_sealed_household(
    _master_key, demo_engine, monkeypatch
) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    calls = _stub_jobs(monkeypatch)

    assert sealed_drain.drain_once(demo_engine, _settings()) == {hh}

    assert calls["imports"] == [{hh}]
    assert calls["snapshot"] == [{hh}]
    assert calls["sync"] == [{hh}]
    assert calls["reports"] == ["weekly", "monthly", "annual"]
    assert calls["study"] == [{hh}]
    # Never a wipe from here: the wipe clears the whole collection and this pass
    # only covers sealed households, so it would delete everyone else's vectors.
    assert calls["index"] == [(False, {hh})]


def test_drain_does_nothing_when_no_sealed_household_is_open(
    _master_key, demo_engine, monkeypatch
) -> None:
    calls = _stub_jobs(monkeypatch)
    assert sealed_drain.drain_once(demo_engine, _settings()) == set()
    assert all(recorded == [] for recorded in calls.values())


def test_one_failing_job_does_not_abandon_the_rest(_master_key, demo_engine, monkeypatch) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    calls = _stub_jobs(monkeypatch)

    def boom(engine, staging, households=None):
        raise RuntimeError("import subsystem down")

    monkeypatch.setattr(sealed_drain.import_processing, "run_pending_imports_once", boom)

    assert sealed_drain.drain_once(demo_engine, _settings()) == {hh}
    assert calls["study"] == [{hh}]  # the tail of the pass still ran


def test_a_session_expiring_mid_drain_is_not_an_error(
    _master_key, demo_engine, monkeypatch
) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    household_crypto._keyring_put(demo_engine, hh, Fernet.generate_key())
    calls = _stub_jobs(monkeypatch)

    def locked_again(engine, households=None):
        raise household_crypto.HouseholdLockedError(hh)

    monkeypatch.setattr(sealed_drain.net_worth_history, "record_snapshot_once", locked_again)

    assert sealed_drain.drain_once(demo_engine, _settings()) == {hh}
    assert calls["study"] == [{hh}]


def test_only_one_drain_runs_at_a_time(_master_key, demo_engine, monkeypatch) -> None:
    """An unlock fires a drain immediately while the periodic tick keeps
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

    monkeypatch.setattr(sealed_drain.import_processing, "run_pending_imports_once", slow_first)

    first = threading.Thread(target=lambda: sealed_drain.drain_once(demo_engine, _settings()))
    first.start()
    assert started.wait(timeout=5)
    second.append(sealed_drain.drain_once(demo_engine, _settings()))
    release.set()
    first.join(timeout=5)

    assert second == [set()]  # the overlapping pass declined rather than doubling up


def test_background_drain_targets_just_that_household(
    _master_key, demo_engine, monkeypatch
) -> None:
    hh = repository.list_households(demo_engine)[0]
    _seal(demo_engine, hh)
    calls = _stub_jobs(monkeypatch)

    # No keyring entry: an explicit household set is honoured as given, which is
    # what the unlock hook passes before the caller's key is visible elsewhere.
    sealed_drain.drain_in_background(demo_engine, _settings(), hh).join(timeout=5)

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


def test_a_filtered_index_pass_refuses_to_wipe() -> None:
    """The wipe clears the entire collection. Combined with a filter it would
    delete every household's vectors and rebuild only the filtered ones."""
    with pytest.raises(ValueError, match="must not wipe"):
        vector_indexing.index_household_data(None, None, wipe=True, households={"hh"})
