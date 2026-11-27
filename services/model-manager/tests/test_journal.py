"""Journal foundations (issue #170 Item 2): atomic writes, fail-closed reads,
monotonic sequencing, tombstone replay, retention. Pure and deterministic —
no Docker, no network, no active runtime mutation."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from family_cfo_model_manager import journal as journal_module
from family_cfo_model_manager.contracts import (
    JOURNAL_SCHEMA_VERSION,
    JobDecision,
    JobOrigin,
    JobRecord,
    JobState,
    ReasonCode,
)
from family_cfo_model_manager.journal import (
    CurrentPointer,
    Journal,
    JournalError,
    RequestTombstone,
)

NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
JOB_ID = "1a1b1c1d-1e1f-2a2b-3c3d-4e4f5a5b5c5d"
REQUEST_ID = "0f0e1d2c-3b4a-5968-7789-abcdef012345"


def _record(**overrides) -> JobRecord:
    fields = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "job_id": JOB_ID,
        "request_id": REQUEST_ID,
        "sequence": 1,
        "origin": JobOrigin.API,
        "runtime_key": "vllm+http://api:8000/v1",
        "request_hash": "ab" * 32,
        "state": JobState.ACCEPTED,
        "decision": JobDecision.UNDECIDED,
        "created_at": NOW,
        "updated_at": NOW,
    }
    fields.update(overrides)
    return JobRecord(**fields)


def _journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / ".runtime-swap" / "v2")


# --- layout and modes ----------------------------------------------------------


def test_layout_directories_are_private(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    assert journal.root.exists()
    for directory in (journal.root, journal.jobs_dir, journal.requests_dir):
        assert (directory.stat().st_mode & 0o777) == 0o700


def test_record_write_is_atomic_private_and_round_trips(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.write_job_record(_record())
    path = journal.job_dir(JOB_ID) / "record.json"
    assert (path.stat().st_mode & 0o777) == 0o600
    assert not list(journal.job_dir(JOB_ID).glob(".tmp-*")), "temp files must not survive"
    record = journal.read_job_record(JOB_ID)
    assert record == _record()
    # A rewritten record replaces, never appends: immutable snapshot reads.
    journal.write_job_record(_record(state=JobState.STAGING, sequence=2))
    assert journal.read_job_record(JOB_ID).state is JobState.STAGING


def test_unsupported_schema_version_fails_closed(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.write_job_record(_record())
    path = journal.job_dir(JOB_ID) / "record.json"
    document = json.loads(path.read_text())
    document["schema_version"] = 1
    path.write_text(json.dumps(document))
    with pytest.raises(JournalError) as excinfo:
        journal.read_job_record(JOB_ID)
    assert excinfo.value.code is ReasonCode.RUNTIME_STATE_CORRUPT
    # And a writer refusing to create a schema-1 record either.
    with pytest.raises(JournalError):
        journal.write_job_record(_record(schema_version=1))


def test_corrupt_record_fails_closed(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.job_dir(JOB_ID).mkdir(parents=True)
    (journal.job_dir(JOB_ID) / "record.json").write_text("{not json")
    with pytest.raises(JournalError) as excinfo:
        journal.read_job_record(JOB_ID)
    assert excinfo.value.code is ReasonCode.RUNTIME_STATE_CORRUPT


def test_traversal_and_garbage_identifiers_never_reach_the_filesystem(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    for bad in ("../../etc/passwd", "not-a-uuid", "..", "a/b"):
        with pytest.raises(JournalError) as excinfo:
            journal.read_job_record(bad)
        assert excinfo.value.code is ReasonCode.RUNTIME_STATE_CORRUPT
        with pytest.raises(JournalError):
            journal.request_path(bad)


def test_symlink_records_are_refused(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    journal.job_dir(JOB_ID).mkdir(parents=True)
    link = journal.job_dir(JOB_ID) / "record.json"
    link.symlink_to(outside)
    with pytest.raises(JournalError) as excinfo:
        journal.read_job_record(JOB_ID)
    assert excinfo.value.code is ReasonCode.RUNTIME_STATE_CORRUPT
    with pytest.raises(JournalError):
        journal.write_job_record(_record())


# --- monotonic sequencing ---------------------------------------------------------


def test_sequence_allocation_is_monotonic_across_jobs(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    with journal.locked():
        first = journal.allocate_sequence()
        second = journal.allocate_sequence()
    assert (first, second) == (1, 2)
    pointer = journal.read_current()
    assert pointer.sequence == 2
    # A fresh journal object over the same root keeps the counter.
    assert Journal(journal.root).read_current().sequence == 2


def test_current_pointer_round_trip(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    assert journal.read_current() == CurrentPointer(
        sequence=0, job_id=None, updated_at=None
    )
    journal.set_current_job(JOB_ID)
    assert journal.read_current().job_id == JOB_ID
    journal.set_current_job(None)
    assert journal.read_current().job_id is None


# --- request tombstones and replay -------------------------------------------------


def test_replay_returns_the_admitted_job(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.record_request(
        RequestTombstone(
            request_id=REQUEST_ID, request_hash="ab" * 32, job_id=JOB_ID, created_at=NOW
        )
    )
    journal.write_job_record(_record())
    assert journal.request_state(REQUEST_ID) == _record()
    # Unknown request: no job, no expiry — a genuinely new request.
    other = "0f0e1d2c-3b4a-5968-7789-abcdef012346"
    assert journal.request_state(other) is None


def test_expired_detail_replay_answers_request_expired(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.record_request(
        RequestTombstone(
            request_id=REQUEST_ID, request_hash="ab" * 32, job_id=JOB_ID, created_at=NOW
        )
    )
    journal.write_job_record(_record())
    # Job detail retention-pruned; the tombstone survives.
    (journal.job_dir(JOB_ID) / "record.json").unlink()
    with pytest.raises(JournalError) as excinfo:
        journal.request_state(REQUEST_ID)
    assert excinfo.value.code is ReasonCode.REQUEST_EXPIRED


# --- retention -----------------------------------------------------------------------


def test_prune_drops_old_terminal_records_but_keeps_rollback_artifacts(
    tmp_path: Path,
) -> None:
    journal = _journal(tmp_path)
    journal.write_job_record(_record(state=JobState.RESTORED, sequence=1))
    journal.write_previous_snapshot(JOB_ID, {"main_model": "old/model"})
    journal.write_target_plan(JOB_ID, {"main_model": "new/model"})
    journal.append_diagnostics(JOB_ID, "diagnostic")
    journal.set_current_job(JOB_ID)

    pruned = journal.prune(NOW + timedelta(days=31))
    assert pruned == (JOB_ID,)
    assert journal.read_job_record(JOB_ID) is None
    assert journal.read_target_plan(JOB_ID) is None
    # The rollback artifact survives the record.
    assert journal.read_previous_snapshot(JOB_ID) == {"main_model": "old/model"}
    assert journal.read_current().job_id is None


def test_prune_keeps_unresolved_and_recent_terminal_jobs(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.write_job_record(_record(sequence=1))
    recent = _record(
        job_id="2a1b1c1d-1e1f-2a2b-3c3d-4e4f5a5b5c5d",
        request_id="2f0e1d2c-3b4a-5968-7789-abcdef012345",
        sequence=2,
        state=JobState.FAILED,
        updated_at=NOW + timedelta(days=10),  # inside the 30-day window
    )
    journal.write_job_record(recent)
    assert journal.prune(NOW + timedelta(days=31)) == ()
    assert journal.read_job_record(JOB_ID) is not None
    assert journal.read_job_record(recent.job_id) is not None


def test_prune_is_idempotent_over_pruned_jobs(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.write_job_record(_record(state=JobState.SUCCEEDED, sequence=1))
    journal.write_previous_snapshot(JOB_ID, {})
    assert journal.prune(NOW + timedelta(days=31)) == (JOB_ID,)
    assert journal.prune(NOW + timedelta(days=31)) == ()


# --- diagnostics ---------------------------------------------------------------------


def test_diagnostics_append_and_rotation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(journal_module, "DIAGNOSTICS_ROTATE_BYTES", 64)
    monkeypatch.setattr(journal_module, "DIAGNOSTICS_KEEP_BYTES", 32)
    journal = _journal(tmp_path)
    for index in range(10):
        journal.append_diagnostics(JOB_ID, f"line-{index}-" + "x" * 20)
    content = (journal.job_dir(JOB_ID) / "diagnostics.log").read_text()
    assert "line-9-" in content
    assert (journal.job_dir(JOB_ID) / "diagnostics.log").stat().st_size < 64
    assert (journal.job_dir(JOB_ID) / "diagnostics.log").stat().st_mode & 0o777 == 0o600


# --- journal lock ----------------------------------------------------------------------


def test_journal_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    order: list[str] = []

    def contender() -> None:
        with journal.locked():
            order.append("second")

    with journal.locked():
        thread = threading.Thread(target=contender)
        thread.start()
        order.append("first-holder")
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert order == ["first-holder", "second"]


def test_journal_directory_is_excluded_from_git(tmp_path: Path) -> None:
    # The journal holds operational deployment state only, but it must never
    # be committable from the project checkout (plan: .gitignore exclusion).
    repo_root = Path(__file__).resolve().parents[3]
    gitignore = (repo_root / ".gitignore").read_text()
    assert ".runtime-swap/" in gitignore
