from __future__ import annotations

import tarfile
import threading
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
from sqlalchemy.engine import Engine

from family_cfo_api import backup_processing, backup_storage, repository, smb_backup
from family_cfo_api.api import backups as backups_api
from family_cfo_api.backup_operation_lock import (
    acquire_backup_operation_lock,
    try_acquire_backup_operation_lock,
)
from family_cfo_api.backup_storage import CapacityObservation
from family_cfo_api.config import Settings


def _config(engine: Engine, settings: Settings) -> backup_processing.BackupExecutionConfig:
    with acquire_backup_operation_lock(engine) as lease:
        return backup_processing.build_backup_execution_config(engine, settings, lease=lease)


def _run(engine: Engine, settings: Settings) -> repository.BackupJobRecord:
    return backup_processing.run_backup_once(engine, settings)


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

    monkeypatch.setattr(
        backup_processing,
        "build_backup_execution_config",
        lambda engine, settings, *, lease: lease.assert_owned() or config,
    )
    record = backup_processing.run_backup_once(demo_file_engine, demo_file_settings)
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


def test_busy_maintenance_skips_and_journals_without_building_config(
    demo_file_engine: Engine, demo_file_settings: Settings, monkeypatch
) -> None:
    config = _config(demo_file_engine, demo_file_settings)
    build_calls = 0
    original_builder = backup_processing.build_backup_execution_config

    def track_builder(engine, settings, *, lease):
        nonlocal build_calls
        build_calls += 1
        return original_builder(engine, settings, lease=lease)

    monkeypatch.setattr(
        backup_processing,
        "build_backup_execution_config",
        track_builder,
    )
    lease = acquire_backup_operation_lock(demo_file_engine)
    try:
        result = backup_processing.run_backup_maintenance(demo_file_engine, demo_file_settings)
    finally:
        lease.release()
    assert result.lock_skipped is True
    assert build_calls == 0
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


def test_unavailable_local_inventory_is_never_journaled_as_success(
    demo_file_engine: Engine, demo_file_settings: Settings, monkeypatch
) -> None:
    unavailable = backup_storage.LocalInventory(
        (), (), (), available=False, failure_code="local_inventory_unavailable"
    )
    monkeypatch.setattr(
        backup_processing, "collect_local_inventory", lambda *args, **kwargs: unavailable
    )

    result = backup_processing.run_backup_maintenance(demo_file_engine, demo_file_settings)

    assert result.local_pruned == 0
    config = _config(demo_file_engine, demo_file_settings)
    events = repository.list_backup_retention_events_for_status(
        demo_file_engine,
        destination_generation=config.local_destination_generation,
    )
    assert any(
        event.action == "inventory_failed" and event.reason == "local_inventory_unavailable"
        for event in events
    )
    assert not any(event.action == "inventory_succeeded" for event in events)


def test_explicit_local_delete_requires_journal_before_filesystem_mutation(
    demo_file_engine: Engine, demo_file_settings: Settings, monkeypatch
) -> None:
    record = _run(demo_file_engine, demo_file_settings)
    assert record.storage_path is not None
    archive = Path(demo_file_settings.backup_dir, record.storage_path)
    original = repository.record_backup_retention_event

    def fail_intent(*args, **kwargs):
        if kwargs["action"] == "delete_pending":
            raise RuntimeError("synthetic journal failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "record_backup_retention_event", fail_intent)
    with pytest.raises(RuntimeError, match="synthetic journal failure"):
        backup_processing.delete_local_backup(
            demo_file_engine,
            record.id,
            demo_file_settings,
        )

    assert archive.is_file()
    assert repository.get_backup_job(demo_file_engine, record.id) is not None


def test_explicit_local_delete_rejects_unsafe_path_before_journaling(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    outside = Path(demo_file_settings.backup_dir).parent / "outside.enc"
    outside.write_bytes(b"evidence")
    record = repository.create_backup_job(demo_file_engine)
    repository.update_backup_job(demo_file_engine, record.id, status="running")
    repository.complete_backup_job_local(
        demo_file_engine,
        record.id,
        storage_path="../outside.enc",
        size_bytes=len(b"evidence"),
        remote_status="skipped",
        app_version=None,
        schema_revision=None,
    )
    config = _config(demo_file_engine, demo_file_settings)

    with pytest.raises(backup_storage.UnsafeBackupPathError):
        backup_processing.delete_local_backup(demo_file_engine, record.id, demo_file_settings)

    assert outside.read_bytes() == b"evidence"
    assert repository.get_backup_job(demo_file_engine, record.id) is not None
    assert not repository.list_unresolved_backup_delete_intents(
        demo_file_engine,
        destination="local",
        destination_generation=config.local_destination_generation,
    )


def test_explicit_local_delete_retains_intent_when_completion_journal_fails(
    demo_file_engine: Engine, demo_file_settings: Settings, monkeypatch
) -> None:
    record = _run(demo_file_engine, demo_file_settings)
    assert record.storage_path is not None
    archive = Path(demo_file_settings.backup_dir, record.storage_path)
    config = _config(demo_file_engine, demo_file_settings)
    original = repository.record_backup_retention_event

    def fail_completion(*args, **kwargs):
        if kwargs["action"] == "explicit_deleted":
            raise RuntimeError("synthetic completion failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "record_backup_retention_event", fail_completion)
    with pytest.raises(RuntimeError, match="synthetic completion failure"):
        backup_processing.delete_local_backup(demo_file_engine, record.id, demo_file_settings)

    assert not archive.exists()
    assert repository.get_backup_job(demo_file_engine, record.id) is None
    assert (
        len(
            repository.list_unresolved_backup_delete_intents(
                demo_file_engine,
                destination="local",
                destination_generation=config.local_destination_generation,
            )
        )
        == 1
    )

    backup_processing.run_backup_maintenance(demo_file_engine, demo_file_settings)
    assert not repository.list_unresolved_backup_delete_intents(
        demo_file_engine,
        destination="local",
        destination_generation=config.local_destination_generation,
    )


def test_explicit_local_delete_retries_metadata_before_resolving_intent(
    demo_file_engine: Engine, demo_file_settings: Settings, monkeypatch
) -> None:
    record = _run(demo_file_engine, demo_file_settings)
    assert record.storage_path is not None
    archive = Path(demo_file_settings.backup_dir, record.storage_path)
    config = _config(demo_file_engine, demo_file_settings)
    original_delete = repository.delete_backup_job
    failed_once = False

    def fail_once(engine: Engine, backup_job_id: str) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("synthetic metadata failure")
        original_delete(engine, backup_job_id)

    monkeypatch.setattr(repository, "delete_backup_job", fail_once)
    with pytest.raises(RuntimeError, match="synthetic metadata failure"):
        backup_processing.delete_local_backup(demo_file_engine, record.id, demo_file_settings)

    assert not archive.exists()
    assert repository.get_backup_job(demo_file_engine, record.id) is not None
    backup_processing.run_backup_maintenance(demo_file_engine, demo_file_settings)
    assert repository.get_backup_job(demo_file_engine, record.id) is None
    assert not repository.list_unresolved_backup_delete_intents(
        demo_file_engine,
        destination="local",
        destination_generation=config.local_destination_generation,
    )


def test_remote_delete_without_job_row_reconciles_durable_intent(
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
        backup_processing,
        "build_backup_execution_config",
        lambda engine, settings, *, lease: lease.assert_owned() or config,
    )
    filename = "c88d2f63-6dc7-4236-a73d-5c09ee640a66.v0.1.0.enc"
    removed: list[str] = []
    monkeypatch.setattr(
        smb_backup,
        "delete",
        lambda target, name, **kwargs: removed.append(name),
    )
    original = repository.record_backup_retention_event

    def fail_completion(*args, **kwargs):
        if kwargs["action"] == "explicit_deleted":
            raise RuntimeError("synthetic completion failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, "record_backup_retention_event", fail_completion)
    with pytest.raises(RuntimeError, match="synthetic completion failure"):
        backup_processing.delete_remote_backup(demo_file_engine, filename, demo_file_settings)

    assert removed == [filename]
    assert (
        len(
            repository.list_unresolved_backup_delete_intents(
                demo_file_engine,
                destination="offbox",
                destination_generation=config.offbox_destination_generation,
            )
        )
        == 1
    )
    with acquire_backup_operation_lock(demo_file_engine) as lease:
        assert (
            backup_processing._reconcile_delete_intents(
                demo_file_engine,
                config,
                lease,
                destination="offbox",
                visible_archive_keys=set(),
            )
            == 1
        )
    assert not repository.list_unresolved_backup_delete_intents(
        demo_file_engine,
        destination="offbox",
        destination_generation=config.offbox_destination_generation,
    )


def test_restore_rolls_back_when_lock_is_lost_after_database_promotion(
    demo_file_engine: Engine, demo_file_settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    class LosingLease:
        owned = True

        def assert_owned(self) -> None:
            if not self.owned:
                raise backup_processing.BackupOperationLockLostError("synthetic lock loss")

    class Adapter:
        def __init__(self) -> None:
            self.restores: list[str] = []

        def dump_database(self, path: Path) -> None:
            path.write_bytes(b"current database")

        def restore_database(self, path: Path) -> None:
            self.restores.append(path.name)
            if path.name == "database.dump":
                lease.owned = False

    lease = LosingLease()
    adapter = Adapter()
    migrated = False
    staged_documents = tmp_path / "staged-documents"
    staged_documents.mkdir()
    monkeypatch.setattr(backup_processing, "decrypt", lambda key, value: b"archive")
    monkeypatch.setattr(
        backup_processing,
        "extract_archive",
        lambda value: (b"database", b"documents", None),
    )
    monkeypatch.setattr(backup_processing, "select_backup_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        backup_processing,
        "_stage_restore_documents",
        lambda *args, **kwargs: staged_documents,
    )

    def migrate(*args, **kwargs) -> None:
        nonlocal migrated
        migrated = True

    monkeypatch.setattr(backup_processing, "_migrate_after_restore", migrate)
    compensation_lease = _ControllableLease()
    monkeypatch.setattr(
        backup_processing,
        "try_acquire_backup_operation_lock",
        lambda engine: compensation_lease,
    )
    config = replace(
        _config(demo_file_engine, demo_file_settings),
        database_url="postgresql+psycopg://localhost/family_cfo",
    )
    with pytest.raises(backup_processing.BackupOperationLockLostError):
        backup_processing._restore_ciphertext_locked(
            demo_file_engine,
            b"ciphertext",
            config=config,
            lease=lease,  # type: ignore[arg-type]
            captured_settings=repository.get_backup_settings(demo_file_engine),
            source_job=None,
        )

    assert adapter.restores == ["database.dump", "database.rollback"]
    assert migrated is False
    assert compensation_lease.owned is False


def test_restore_does_not_compensate_without_reacquiring_exclusive_ownership(
    demo_file_engine: Engine, demo_file_settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    class LosingLease:
        owned = True

        def assert_owned(self) -> None:
            if not self.owned:
                raise backup_processing.BackupOperationLockLostError("synthetic lock loss")

    class Adapter:
        def __init__(self) -> None:
            self.restores: list[str] = []

        def dump_database(self, path: Path) -> None:
            path.write_bytes(b"current database")

        def restore_database(self, path: Path) -> None:
            self.restores.append(path.name)
            if path.name == "database.dump":
                lease.owned = False

    lease = LosingLease()
    adapter = Adapter()
    staged_documents = tmp_path / "staged-documents-contended"
    staged_documents.mkdir()
    monkeypatch.setattr(backup_processing, "decrypt", lambda key, value: b"archive")
    monkeypatch.setattr(
        backup_processing,
        "extract_archive",
        lambda value: (b"database", b"documents", None),
    )
    monkeypatch.setattr(backup_processing, "select_backup_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        backup_processing,
        "_stage_restore_documents",
        lambda *args, **kwargs: staged_documents,
    )
    monkeypatch.setattr(
        backup_processing,
        "try_acquire_backup_operation_lock",
        lambda engine: None,
    )
    config = replace(
        _config(demo_file_engine, demo_file_settings),
        database_url="postgresql+psycopg://localhost/family_cfo",
    )

    with pytest.raises(backup_processing.BackupRestoreRollbackError) as raised:
        backup_processing._restore_ciphertext_locked(
            demo_file_engine,
            b"ciphertext",
            config=config,
            lease=lease,  # type: ignore[arg-type]
            captured_settings=repository.get_backup_settings(demo_file_engine),
            source_job=None,
        )

    assert raised.value.kind == "rollback"
    # The archive promotion happened, but this process never overwrote the new
    # owner's work with an unowned rollback attempt.
    assert adapter.restores == ["database.dump"]


def test_remote_restore_holds_one_lock_across_selection_download_and_restore(
    demo_file_engine: Engine, demo_file_settings: Settings, monkeypatch
) -> None:
    held = False
    acquired = 0

    class Lease:
        def __enter__(self):
            nonlocal held
            held = True
            return self

        def __exit__(self, *args) -> None:
            nonlocal held
            held = False

        def assert_owned(self) -> None:
            assert held

    def acquire(engine: Engine):
        nonlocal acquired
        acquired += 1
        return Lease()

    filename = "c88d2f63-6dc7-4236-a73d-5c09ee640a66.v0.1.0.enc"
    target = smb_backup.SmbTarget(
        host="nas.invalid",
        share="backups",
        folder=None,
        username="backup-user",
        password="synthetic",
    )
    config = replace(_config(demo_file_engine, demo_file_settings), smb_target=target)
    monkeypatch.setattr(backup_processing, "acquire_backup_operation_lock", acquire)
    monkeypatch.setattr(
        backup_processing,
        "build_backup_execution_config",
        lambda engine, settings, *, lease: lease.assert_owned() or config,
    )
    monkeypatch.setattr(
        smb_backup,
        "list_backups",
        lambda target: assert_held_and_return(
            held,
            [{"filename": filename, "modified_at": 1}],
        ),
    )
    monkeypatch.setattr(
        smb_backup,
        "download",
        lambda target, name: assert_held_and_return(held, b"ciphertext"),
    )

    def assert_restore_locked(*args, lease, complete, **kwargs):
        assert held
        lease.assert_owned()
        return complete()

    monkeypatch.setattr(backup_processing, "_restore_ciphertext_locked", assert_restore_locked)
    capacity = CapacityObservation(
        "unknown",
        None,
        None,
        0,
        None,
        None,
        datetime.now(UTC),
        "capacity_unknown",
    )
    monkeypatch.setattr(
        backup_processing,
        "_remote_capacity_for_config",
        lambda *args, **kwargs: assert_held_and_return(held, capacity),
    )
    finalized = False

    def finalize(boundary: backup_processing.RestoreBoundary) -> None:
        nonlocal finalized
        assert held
        assert boundary == (3, "restore summary")
        finalized = True

    result = backup_processing.restore_remote_backup(
        demo_file_engine,
        filename,
        demo_file_settings,
        before_restore=lambda snapshot: assert_held_and_return(held, (3, "restore summary")),
        finalize=finalize,
    )

    assert result.boundary == (3, "restore summary")
    assert result.capacity == capacity
    assert finalized is True
    assert acquired == 1
    assert held is False


def assert_held_and_return(held: bool, value):
    assert held
    return value


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
    boundaries: list[backup_processing.RestoreBoundary] = []

    def before_restore(snapshot_at: datetime | None) -> backup_processing.RestoreBoundary:
        assert snapshot_at is not None
        assert try_acquire_backup_operation_lock(demo_file_engine) is None
        return (2, "restore boundary")

    def finalize(boundary: backup_processing.RestoreBoundary) -> None:
        assert try_acquire_backup_operation_lock(demo_file_engine) is None
        boundaries.append(boundary)

    result = backup_processing.restore_backup(
        demo_file_engine,
        source.id,
        demo_file_settings,
        before_restore=before_restore,
        finalize=finalize,
    )

    assert result.job.id == source.id
    assert boundaries == [(2, "restore boundary")]
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


@pytest.mark.parametrize(
    "extraction_failure",
    [
        OSError(28, "synthetic disk full path"),
        PermissionError("synthetic permission path"),
        tarfile.ReadError("synthetic corrupt archive path"),
    ],
    ids=["disk-full", "permission", "invalid-tar"],
)
def test_document_staging_failure_preserves_live_database_and_tree(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
    monkeypatch,
    extraction_failure: Exception,
) -> None:
    live_directory = Path(demo_file_settings.import_staging_dir)
    live_directory.mkdir(parents=True)
    (live_directory / "archived-only.txt").write_text("archived")
    source = _run(demo_file_engine, demo_file_settings)
    (live_directory / "archived-only.txt").unlink()
    (live_directory / "current-only.txt").write_text("current")
    sentinel = repository.create_backup_job(demo_file_engine)

    def fail_after_partial_extract(data: bytes, directory: str) -> None:
        Path(directory, "partial.txt").write_text("partial")
        raise extraction_failure

    monkeypatch.setattr(backup_processing, "_untar_directory", fail_after_partial_extract)

    with pytest.raises(backup_processing.BackupDocumentRestoreError) as raised:
        backup_processing.restore_backup(demo_file_engine, source.id, demo_file_settings)

    assert raised.value.kind == "documents"
    assert "synthetic" not in str(raised.value)
    assert repository.get_backup_job(demo_file_engine, sentinel.id) is not None
    assert (live_directory / "current-only.txt").read_text() == "current"
    assert not (live_directory / "archived-only.txt").exists()
    assert not list(live_directory.parent.glob(".import-staging.restore-new-*"))


def test_document_promotion_failure_rolls_back_database_and_live_tree(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
    monkeypatch,
) -> None:
    live_directory = Path(demo_file_settings.import_staging_dir)
    live_directory.mkdir(parents=True)
    (live_directory / "archived-only.txt").write_text("archived")
    source = _run(demo_file_engine, demo_file_settings)
    (live_directory / "archived-only.txt").unlink()
    (live_directory / "current-only.txt").write_text("current")
    sentinel = repository.create_backup_job(demo_file_engine)
    original_replace = backup_processing.os.replace

    def fail_new_tree_promotion(source_path, destination_path) -> None:
        source_candidate = Path(source_path)
        destination_candidate = Path(destination_path)
        if destination_candidate == live_directory and source_candidate.name.startswith(
            ".import-staging.restore-new-"
        ):
            raise OSError(28, "synthetic promotion disk failure")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(backup_processing.os, "replace", fail_new_tree_promotion)

    with pytest.raises(backup_processing.BackupDocumentRestoreError) as raised:
        backup_processing.restore_backup(demo_file_engine, source.id, demo_file_settings)

    assert raised.value.kind == "documents"
    assert "synthetic" not in str(raised.value)
    assert repository.get_backup_job(demo_file_engine, sentinel.id) is not None
    restored_source = repository.get_backup_job(demo_file_engine, source.id)
    assert restored_source is not None
    assert restored_source.status == "completed"
    assert (live_directory / "current-only.txt").read_text() == "current"
    assert not (live_directory / "archived-only.txt").exists()
    assert not list(live_directory.parent.glob(".import-staging.restore-*"))


def test_non_sqlite_migration_failure_restores_verified_database_preimage(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
    monkeypatch,
) -> None:
    source = _run(demo_file_engine, demo_file_settings)
    assert source.storage_path is not None
    ciphertext = Path(demo_file_settings.backup_dir, source.storage_path).read_bytes()
    sentinel = repository.create_backup_job(demo_file_engine)
    database_path = Path(demo_file_engine.url.database or "")

    class FileAdapter:
        def __init__(self) -> None:
            self.restored_names: list[str] = []

        def dump_database(self, destination: Path) -> None:
            destination.write_bytes(database_path.read_bytes())

        def restore_database(self, source_path: Path) -> None:
            self.restored_names.append(source_path.name)
            database_path.write_bytes(source_path.read_bytes())

    adapter = FileAdapter()
    config = replace(
        _config(demo_file_engine, demo_file_settings),
        database_url="postgresql+psycopg://localhost/family_cfo",
    )
    captured_settings = repository.get_backup_settings(demo_file_engine)
    monkeypatch.setattr(backup_processing, "select_backup_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        backup_processing,
        "_migrate_after_restore",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            backup_processing.BackupMigrationError("nonzero_exit")
        ),
    )

    with (
        acquire_backup_operation_lock(demo_file_engine) as lease,
        pytest.raises(backup_processing.BackupMigrationError),
    ):
        backup_processing._restore_ciphertext_locked(
            demo_file_engine,
            ciphertext,
            config=config,
            lease=lease,
            captured_settings=captured_settings,
            source_job=None,
        )

    assert adapter.restored_names == ["database.dump", "database.rollback"]
    assert repository.get_backup_job(demo_file_engine, sentinel.id) is not None
    restored_settings = repository.get_backup_settings(demo_file_engine)
    assert restored_settings.frequency == captured_settings.frequency
    assert restored_settings.retention_review_required is True
    assert restored_settings.retention_activated_at is None
    assert (
        restored_settings.local_destination_generation
        != captured_settings.local_destination_generation
    )
    assert (
        restored_settings.offbox_destination_generation
        != captured_settings.offbox_destination_generation
    )


def test_noop_database_rollback_fails_archive_external_marker_verification(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
    monkeypatch,
) -> None:
    source = _run(demo_file_engine, demo_file_settings)
    assert source.storage_path is not None
    ciphertext = Path(demo_file_settings.backup_dir, source.storage_path).read_bytes()
    sentinel = repository.create_backup_job(demo_file_engine)
    database_path = Path(demo_file_engine.url.database or "")

    class NoopRollbackAdapter:
        def __init__(self) -> None:
            self.restored_names: list[str] = []

        def dump_database(self, destination: Path) -> None:
            destination.write_bytes(database_path.read_bytes())

        def restore_database(self, source_path: Path) -> None:
            self.restored_names.append(source_path.name)
            if source_path.name == "database.dump":
                database_path.write_bytes(source_path.read_bytes())
            # The rollback call deliberately does nothing. Matching schema heads
            # are insufficient: the preflight settings nonce must also return.

    adapter = NoopRollbackAdapter()
    config = replace(
        _config(demo_file_engine, demo_file_settings),
        database_url="postgresql+psycopg://localhost/family_cfo",
    )
    monkeypatch.setattr(backup_processing, "select_backup_adapter", lambda *args, **kwargs: adapter)
    monkeypatch.setattr(
        backup_processing,
        "_migrate_after_restore",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            backup_processing.BackupMigrationError("nonzero_exit")
        ),
    )

    with (
        acquire_backup_operation_lock(demo_file_engine) as lease,
        pytest.raises(backup_processing.BackupRestoreRollbackError) as raised,
    ):
        backup_processing._restore_ciphertext_locked(
            demo_file_engine,
            ciphertext,
            config=config,
            lease=lease,
            captured_settings=repository.get_backup_settings(demo_file_engine),
            source_job=None,
        )

    assert raised.value.kind == "rollback"
    assert adapter.restored_names == ["database.dump", "database.rollback"]
    assert repository.get_backup_job(demo_file_engine, sentinel.id) is None


def test_restore_finalizer_failure_rolls_back_database_and_documents(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
) -> None:
    live_directory = Path(demo_file_settings.import_staging_dir)
    live_directory.mkdir(parents=True)
    (live_directory / "archived-only.txt").write_text("archived")
    source = _run(demo_file_engine, demo_file_settings)
    (live_directory / "archived-only.txt").unlink()
    (live_directory / "current-only.txt").write_text("current")
    sentinel = repository.create_backup_job(demo_file_engine)

    def fail_finalizer(boundary: backup_processing.RestoreBoundary) -> None:
        assert boundary == (0, "synthetic boundary")
        raise RuntimeError("synthetic private audit failure")

    with pytest.raises(backup_processing.BackupRestoreFinalizationError) as raised:
        backup_processing.restore_backup(
            demo_file_engine,
            source.id,
            demo_file_settings,
            before_restore=lambda snapshot: (0, "synthetic boundary"),
            finalize=fail_finalizer,
        )

    assert raised.value.kind == "finalization"
    assert "synthetic" not in str(raised.value)
    assert repository.get_backup_job(demo_file_engine, sentinel.id) is not None
    assert (live_directory / "current-only.txt").read_text() == "current"
    assert not (live_directory / "archived-only.txt").exists()
    assert not list(live_directory.parent.glob(".import-staging.restore-*"))


def test_restore_reacquires_before_rollback_when_finalizer_masks_lease_loss(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
    monkeypatch,
) -> None:
    live_directory = Path(demo_file_settings.import_staging_dir)
    live_directory.mkdir(parents=True)
    (live_directory / "archived-only.txt").write_text("archived")
    source = _run(demo_file_engine, demo_file_settings)
    (live_directory / "archived-only.txt").unlink()
    (live_directory / "current-only.txt").write_text("current")
    sentinel = repository.create_backup_job(demo_file_engine)
    lease = _ControllableLease()
    compensation_lease = _ControllableLease()
    monkeypatch.setattr(backup_processing, "acquire_backup_operation_lock", lambda engine: lease)
    monkeypatch.setattr(
        backup_processing,
        "try_acquire_backup_operation_lock",
        lambda engine: compensation_lease,
    )

    def lose_then_fail(boundary: backup_processing.RestoreBoundary) -> None:
        lease.owned = False
        raise RuntimeError("synthetic private finalizer failure")

    with pytest.raises(backup_processing.BackupRestoreFinalizationError) as raised:
        backup_processing.restore_backup(
            demo_file_engine,
            source.id,
            demo_file_settings,
            before_restore=lambda snapshot: (0, "synthetic boundary"),
            finalize=lose_then_fail,
        )

    assert raised.value.kind == "finalization"
    assert "synthetic" not in str(raised.value)
    assert compensation_lease.owned is False
    assert repository.get_backup_job(demo_file_engine, sentinel.id) is not None
    assert (live_directory / "current-only.txt").read_text() == "current"
    assert not (live_directory / "archived-only.txt").exists()


class _ControllableLease:
    def __init__(self) -> None:
        self.owned = True

    def assert_owned(self) -> None:
        if not self.owned:
            raise backup_processing.BackupOperationLockLostError("synthetic lock loss")

    def release(self) -> None:
        self.owned = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def test_create_certifies_lease_after_response_job_read(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
    monkeypatch,
) -> None:
    lease = _ControllableLease()
    original_get = repository.get_backup_job
    terminal_reads = 0

    def lose_during_final_read(engine: Engine, backup_job_id: str):
        nonlocal terminal_reads
        record = original_get(engine, backup_job_id)
        if record is not None and record.status in {"completed", "failed"}:
            terminal_reads += 1
            if terminal_reads == 2:
                lease.owned = False
        return record

    monkeypatch.setattr(backup_processing, "acquire_backup_operation_lock", lambda engine: lease)
    monkeypatch.setattr(repository, "get_backup_job", lose_during_final_read)

    with pytest.raises(backup_processing.BackupOperationLockLostError):
        backup_processing.run_backup_once(demo_file_engine, demo_file_settings)

    assert terminal_reads == 2


def test_local_restore_certifies_lease_after_response_job_read(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
    monkeypatch,
) -> None:
    live_directory = Path(demo_file_settings.import_staging_dir)
    live_directory.mkdir(parents=True)
    (live_directory / "archived-only.txt").write_text("archived")
    source = _run(demo_file_engine, demo_file_settings)
    (live_directory / "archived-only.txt").unlink()
    (live_directory / "current-only.txt").write_text("current")
    sentinel = repository.create_backup_job(demo_file_engine)
    lease = _ControllableLease()
    compensation_lease = _ControllableLease()
    original_get = repository.get_backup_job
    source_reads = 0

    def lose_during_final_read(engine: Engine, backup_job_id: str):
        nonlocal source_reads
        record = original_get(engine, backup_job_id)
        if backup_job_id == source.id and record is not None:
            source_reads += 1
            if source_reads == 3:
                lease.owned = False
        return record

    monkeypatch.setattr(backup_processing, "acquire_backup_operation_lock", lambda engine: lease)
    monkeypatch.setattr(
        backup_processing,
        "try_acquire_backup_operation_lock",
        lambda engine: compensation_lease,
    )
    monkeypatch.setattr(repository, "get_backup_job", lose_during_final_read)

    with pytest.raises(backup_processing.BackupOperationLockLostError):
        backup_processing.restore_backup(demo_file_engine, source.id, demo_file_settings)

    assert source_reads == 3
    assert compensation_lease.owned is False
    assert original_get(demo_file_engine, sentinel.id) is not None
    assert (live_directory / "current-only.txt").read_text() == "current"
    assert not (live_directory / "archived-only.txt").exists()
