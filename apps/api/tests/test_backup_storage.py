from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import Engine

from family_cfo_api import __version__ as APP_VERSION
from family_cfo_api import backup_storage, repository
from family_cfo_api.backup_operation_lock import acquire_backup_operation_lock


def _completed_job(
    engine: Engine,
    backup_dir: str,
    *,
    size: int,
    storage_path: str | None = None,
) -> repository.BackupJobRecord:
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    job = repository.create_backup_job(engine)
    repository.update_backup_job(engine, job.id, status="running")
    name = storage_path or f"{job.id}.enc"
    if storage_path is None:
        Path(backup_dir, name).write_bytes(b"x" * size)
    repository.complete_backup_job_local(
        engine,
        job.id,
        storage_path=name,
        size_bytes=size,
        remote_status="skipped",
        app_version=APP_VERSION,
        schema_revision=None,
    )
    record = repository.get_backup_job(engine, job.id)
    assert record is not None
    return record


def test_inventory_rejects_database_path_escape(
    demo_file_engine: Engine, demo_file_settings
) -> None:
    outside = Path(demo_file_settings.backup_dir).parent / "outside.enc"
    outside.write_bytes(b"evidence")
    record = _completed_job(
        demo_file_engine,
        demo_file_settings.backup_dir,
        size=len(b"evidence"),
        storage_path="../outside.enc",
    )

    inventory = backup_storage.collect_local_inventory(
        demo_file_engine, demo_file_settings.backup_dir
    )
    entry = next(item for item in inventory.entries if item.record.id == record.id)
    assert "unsafe_storage_path" in entry.item.anomaly_codes
    assert entry.item.readable is False
    assert outside.read_bytes() == b"evidence"


def test_inventory_protects_symlink_archive(demo_file_engine: Engine, demo_file_settings) -> None:
    job = repository.create_backup_job(demo_file_engine)
    repository.update_backup_job(demo_file_engine, job.id, status="running")
    outside = Path(demo_file_settings.backup_dir).parent / "outside.enc"
    outside.write_bytes(b"evidence")
    Path(demo_file_settings.backup_dir).mkdir(parents=True, exist_ok=True)
    archive = Path(demo_file_settings.backup_dir) / f"{job.id}.enc"
    archive.symlink_to(outside)
    repository.complete_backup_job_local(
        demo_file_engine,
        job.id,
        storage_path=archive.name,
        size_bytes=len(b"evidence"),
        remote_status="skipped",
        app_version=APP_VERSION,
        schema_revision=None,
    )

    inventory = backup_storage.collect_local_inventory(
        demo_file_engine, demo_file_settings.backup_dir
    )
    entry = next(item for item in inventory.entries if item.record.id == job.id)
    assert "unsafe_storage_path" in entry.item.anomaly_codes
    assert outside.read_bytes() == b"evidence"


def test_inventory_reports_unavailable_directory_instead_of_empty(
    demo_file_engine: Engine, tmp_path: Path
) -> None:
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_bytes(b"evidence")

    inventory = backup_storage.collect_local_inventory(
        demo_file_engine, str(not_a_directory)
    )

    assert inventory.available is False
    assert inventory.failure_code == "local_inventory_unavailable"
    with pytest.raises(backup_storage.LocalInventoryUnavailableError):
        inventory.require_available()


def test_capacity_uses_caller_available_bytes_and_warning_math(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        os,
        "statvfs",
        lambda path: SimpleNamespace(
            f_blocks=100,
            f_frsize=1024,
            f_bavail=20,
        ),
    )

    observation = backup_storage.query_local_capacity(
        str(tmp_path), reserve_bytes=10_000, estimated_next_backup_bytes=8_000
    )
    assert observation.total_bytes == 102_400
    assert observation.available_bytes == 20_480
    assert observation.status == "warning"
    assert observation.can_accept_estimated_backup is True


def test_estimate_uses_largest_three_and_rounds_up_to_mib(
    demo_file_engine: Engine, demo_file_settings
) -> None:
    for size in (10, 20, 30, 40):
        _completed_job(
            demo_file_engine,
            demo_file_settings.backup_dir,
            size=size,
        )
    inventory = backup_storage.collect_local_inventory(
        demo_file_engine, demo_file_settings.backup_dir
    )
    assert backup_storage.estimate_next_backup_bytes(inventory) == 1024 * 1024


def test_atomic_promotion_cleans_partial_when_replace_fails(
    demo_file_engine: Engine, demo_file_settings, monkeypatch
) -> None:
    job = repository.create_backup_job(demo_file_engine)
    lease = acquire_backup_operation_lock(demo_file_engine)
    monkeypatch.setattr(os, "replace", lambda source, target: (_ for _ in ()).throw(OSError()))
    try:
        with pytest.raises(OSError):
            backup_storage.atomic_promote_local_archive(
                demo_file_settings.backup_dir, job.id, b"ciphertext", lease
            )
    finally:
        lease.release()
    assert not Path(demo_file_settings.backup_dir, f"{job.id}.enc.partial").exists()
    assert not Path(demo_file_settings.backup_dir, f"{job.id}.enc").exists()


def test_atomic_promotion_preserves_preexisting_partial(
    demo_file_engine: Engine, demo_file_settings
) -> None:
    job = repository.create_backup_job(demo_file_engine)
    partial = Path(demo_file_settings.backup_dir, f"{job.id}.enc.partial")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"existing evidence")
    lease = acquire_backup_operation_lock(demo_file_engine)
    try:
        with pytest.raises(FileExistsError):
            backup_storage.atomic_promote_local_archive(
                demo_file_settings.backup_dir, job.id, b"new ciphertext", lease
            )
    finally:
        lease.release()
    assert partial.read_bytes() == b"existing evidence"
    assert not Path(demo_file_settings.backup_dir, f"{job.id}.enc").exists()
