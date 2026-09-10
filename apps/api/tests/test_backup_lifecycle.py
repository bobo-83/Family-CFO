from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
from sqlalchemy.engine import Engine

from family_cfo_api import backup_processing, repository, smb_backup
from family_cfo_api.api import backups as backups_api
from family_cfo_api.backup_operation_lock import (
    acquire_backup_operation_lock,
    try_acquire_backup_operation_lock,
)
from family_cfo_api.backup_storage import CapacityObservation
from family_cfo_api.config import Settings


def _config(engine: Engine, settings: Settings) -> backup_processing.BackupExecutionConfig:
    return backup_processing.build_backup_execution_config(engine, settings)


def _run(engine: Engine, settings: Settings) -> repository.BackupJobRecord:
    job_id = backup_processing.run_backup_once(engine, _config(engine, settings))
    record = repository.get_backup_job(engine, job_id)
    assert record is not None
    return record


def test_execution_config_is_immutable(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    config = _config(demo_file_engine, demo_file_settings)
    with pytest.raises(FrozenInstanceError):
        config.frequency = "off"  # type: ignore[misc]


def test_known_local_capacity_failure_stops_before_archive_promotion(
    demo_file_engine: Engine, demo_file_settings: Settings, monkeypatch
) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        backup_processing,
        "query_local_capacity",
        lambda *args, **kwargs: CapacityObservation(
            "insufficient",
            100,
            1,
            10,
            50,
            False,
            now,
            "capacity_insufficient",
        ),
    )

    record = _run(demo_file_engine, demo_file_settings)
    assert record.status == "failed"
    assert record.error_message == "BackupConfigurationError: local backup capacity is insufficient"
    assert not list(Path(demo_file_settings.backup_dir).glob("*.enc"))


def test_smb_failure_preserves_completed_local_archive(
    demo_file_engine: Engine, demo_file_settings: Settings, monkeypatch
) -> None:
    target = smb_backup.SmbTarget(
        host="nas.invalid",
        share="backups",
        folder=None,
        username="backup-user",
        password="synthetic",
    )
    config = replace(_config(demo_file_engine, demo_file_settings), smb_target=target)
    monkeypatch.setattr(
        smb_backup,
        "list_inventory",
        lambda target: smb_backup.SmbInventory((), ()),
    )
    monkeypatch.setattr(
        smb_backup,
        "query_capacity",
        lambda target: (_ for _ in ()).throw(
            smb_backup.SmbCapacityError(
                "capacity_unsupported",
                "Capacity information is not available for this destination.",
            )
        ),
    )
    monkeypatch.setattr(
        smb_backup,
        "upload",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            smb_backup.SmbStorageError("destination_unreachable", "Synology unavailable.")
        ),
    )

    job_id = backup_processing.run_backup_once(demo_file_engine, config)
    record = repository.get_backup_job(demo_file_engine, job_id)
    assert record is not None
    assert record.status == "completed"
    assert record.remote_status == "failed"
    assert record.storage_path is not None
    assert Path(demo_file_settings.backup_dir, record.storage_path).is_file()


def test_maintenance_runs_when_frequency_is_off_and_reconciles_missing_file(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    record = _run(demo_file_engine, demo_file_settings)
    assert record.storage_path is not None
    Path(demo_file_settings.backup_dir, record.storage_path).unlink()
    stored = repository.get_backup_settings(demo_file_engine)
    repository.update_backup_settings(
        demo_file_engine,
        {"frequency": "off"},
        expected_updated_at=stored.updated_at,
    )

    assert backup_processing.run_due_backups(demo_file_engine, demo_file_settings) == 0
    result = backup_processing.run_backup_maintenance(demo_file_engine, demo_file_settings)
    assert result.reconciled_jobs == 1
    reconciled = repository.get_backup_job(demo_file_engine, record.id)
    assert reconciled is not None
    assert reconciled.prune_reason == "missing_file"


def test_busy_maintenance_skips_and_journals(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    config = _config(demo_file_engine, demo_file_settings)
    lease = acquire_backup_operation_lock(demo_file_engine)
    try:
        result = backup_processing.run_backup_maintenance(demo_file_engine, demo_file_settings)
    finally:
        lease.release()
    assert result.lock_skipped is True
    events = repository.list_backup_retention_events_for_status(
        demo_file_engine,
        destination_generation=config.local_destination_generation,
    )
    assert any(event.action == "lock_skipped" for event in events)


@pytest.mark.anyio
async def test_cancelled_waiter_does_not_abandon_thread_owned_lock(
    demo_file_engine: Engine,
) -> None:
    started = threading.Event()
    finish = threading.Event()
    scope_ready = anyio.Event()
    scope_holder = []

    def blocking_operation() -> None:
        with acquire_backup_operation_lock(demo_file_engine):
            started.set()
            finish.wait(timeout=5)

    async def waiter() -> None:
        with anyio.CancelScope() as scope:
            scope_holder.append(scope)
            scope_ready.set()
            await backups_api._run_sync(blocking_operation)

    async with anyio.create_task_group() as group:
        group.start_soon(waiter)
        await scope_ready.wait()
        while not started.is_set():
            await anyio.sleep(0.01)
        scope_holder[0].cancel()
        await anyio.sleep(0.05)
        assert try_acquire_backup_operation_lock(demo_file_engine) is None
        finish.set()


def test_restore_preserves_current_settings_rotates_generations_and_pauses_pruning(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    source = _run(demo_file_engine, demo_file_settings)
    before = repository.get_backup_settings(demo_file_engine)
    current = repository.update_backup_settings(
        demo_file_engine,
        {"frequency": "weekly", "local_min_free_bytes": 123},
        expected_updated_at=before.updated_at,
    )
    config = _config(demo_file_engine, demo_file_settings)

    backup_processing.restore_backup(demo_file_engine, source.id, config)

    restored = repository.get_backup_settings(demo_file_engine)
    assert restored.frequency == "weekly"
    assert restored.local_min_free_bytes == 123
    assert restored.retention_review_required is True
    assert restored.retention_activated_at is None
    assert restored.local_destination_generation != current.local_destination_generation
    assert restored.offbox_destination_generation != current.offbox_destination_generation
    restored_source = repository.get_backup_job(demo_file_engine, source.id)
    assert restored_source is not None
    assert restored_source.status == "completed"
    events = repository.list_backup_retention_events_for_status(
        demo_file_engine,
        destination_generation=restored.local_destination_generation,
    )
    assert any(event.action == "restore_reset" for event in events)
