from __future__ import annotations

import io
import logging
import os
import tarfile
import tempfile
from pathlib import Path

from family_cfo_backup import (
    BackupAdapter,
    BackupCommandError,
    BackupEncryptionError,
    PgDumpBackupAdapter,
    SqliteFileBackupAdapter,
    build_archive,
    decrypt,
    encrypt,
    extract_archive,
)
from sqlalchemy.engine import Engine

from family_cfo_api import repository

logger = logging.getLogger(__name__)


class BackupConfigurationError(ValueError):
    """Raised for a missing encryption key or an unsupported database_url scheme."""


def select_backup_adapter(database_url: str) -> BackupAdapter:
    scheme = database_url.split("://", 1)[0].split("+")[0]
    if scheme == "postgresql":
        return PgDumpBackupAdapter(database_url)
    if scheme == "sqlite":
        path = database_url.split("///", 1)[-1] if "///" in database_url else ""
        if not path or path == ":memory:":
            raise BackupConfigurationError(
                "backups require a file-based sqlite database_url in this environment"
            )
        return SqliteFileBackupAdapter(Path(path))
    raise BackupConfigurationError(f"no backup adapter for database scheme {scheme!r}")


def _tar_directory(directory: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        if os.path.isdir(directory):
            tar.add(directory, arcname=".")
    return buffer.getvalue()


def _untar_directory(data: bytes, directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    buffer = io.BytesIO(data)
    with tarfile.open(fileobj=buffer, mode="r") as tar:
        tar.extractall(directory, filter="data")


def run_backup_once(
    engine: Engine,
    *,
    database_url: str,
    staging_dir: str,
    backup_dir: str,
    encryption_key: str | None,
    retention_count: int,
) -> str:
    """Create one encrypted backup archive. Returns the backup_job id regardless of outcome.

    Called directly by tests (synchronous, deterministic) and polled by the
    worker's scheduled job, following the same pattern M7 established for
    import processing.
    """
    job = repository.create_backup_job(engine)
    repository.update_backup_job(engine, job.id, status="running")

    try:
        if not encryption_key:
            raise BackupConfigurationError("FAMILY_CFO_BACKUP_ENCRYPTION_KEY is not configured")

        adapter = select_backup_adapter(database_url)
        os.makedirs(backup_dir, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp_dir:
            dump_path = Path(tmp_dir) / "database.dump"
            adapter.dump_database(dump_path)
            database_dump = dump_path.read_bytes()

        documents_tar = _tar_directory(staging_dir)
        archive = build_archive(database_dump, documents_tar)
        ciphertext = encrypt(encryption_key, archive)

        storage_path = f"{job.id}.enc"
        full_path = os.path.join(backup_dir, storage_path)
        with open(full_path, "wb") as backup_file:
            backup_file.write(ciphertext)

        repository.update_backup_job(
            engine,
            job.id,
            status="completed",
            storage_path=storage_path,
            size_bytes=len(ciphertext),
        )
        logger.info("backup completed backup_id=%s size_bytes=%s", job.id, len(ciphertext))
    except (BackupCommandError, BackupEncryptionError, BackupConfigurationError, OSError) as exc:
        repository.update_backup_job(
            engine, job.id, status="failed", error_message=f"{type(exc).__name__}: {exc}"
        )
        logger.warning("backup failed backup_id=%s error_type=%s", job.id, type(exc).__name__)
        return job.id

    _apply_retention(engine, backup_dir, retention_count)
    return job.id


def _apply_retention(engine: Engine, backup_dir: str, retention_count: int) -> None:
    completed = repository.list_completed_backup_jobs_for_retention(engine)
    excess = completed[: max(0, len(completed) - retention_count)]
    for job in excess:
        if job.storage_path:
            full_path = os.path.join(backup_dir, job.storage_path)
            if os.path.exists(full_path):
                os.remove(full_path)
        repository.mark_backup_job_pruned(engine, job.id)
        logger.info("backup pruned backup_id=%s", job.id)


def restore_backup(
    engine: Engine,
    backup_job_id: str,
    *,
    database_url: str,
    staging_dir: str,
    backup_dir: str,
    encryption_key: str | None,
) -> None:
    job = repository.get_backup_job(engine, backup_job_id)
    if job is None:
        raise ValueError(f"backup job {backup_job_id} not found")
    if job.status != "completed" or not job.storage_path:
        raise ValueError(
            f"backup job {backup_job_id} is not a completed backup with a stored archive"
        )
    if not encryption_key:
        raise BackupConfigurationError("FAMILY_CFO_BACKUP_ENCRYPTION_KEY is not configured")

    full_path = os.path.join(backup_dir, job.storage_path)
    with open(full_path, "rb") as backup_file:
        ciphertext = backup_file.read()

    archive = decrypt(encryption_key, ciphertext)
    database_dump, documents_tar = extract_archive(archive)

    adapter = select_backup_adapter(database_url)
    with tempfile.TemporaryDirectory() as tmp_dir:
        dump_path = Path(tmp_dir) / "database.dump"
        dump_path.write_bytes(database_dump)
        adapter.restore_database(dump_path)

    _untar_directory(documents_tar, staging_dir)
    logger.info("backup restored backup_id=%s", backup_job_id)
