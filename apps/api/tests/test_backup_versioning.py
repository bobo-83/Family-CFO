"""Backup version labels + restore-compatibility guard.

Every archive seals a manifest (app_version, schema_revision) inside the
encrypted tar; the job row records the same pair so the UI can label each
backup. Restoring an archive from a NEWER app than the box runs is refused
(there is no schema downgrade path); an older-but-known archive restores and
is migrated forward; a manifest-less legacy archive restores as-is.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from family_cfo_backup import build_archive, decrypt, encrypt, extract_archive
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from family_cfo_api import __version__ as APP_VERSION
from family_cfo_api import backup_processing, repository
from family_cfo_api.config import Settings

API_DIR = Path(__file__).resolve().parents[1]
PRE_RETENTION_REVISION = "0092_uppercase_currency_codes"


def _run_backup(engine: Engine, settings: Settings) -> repository.BackupJobRecord:
    return backup_processing.run_backup_once(engine, settings)


def test_backup_seals_manifest_and_labels_job(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    record = _run_backup(demo_file_engine, demo_file_settings)

    assert record.status == "completed"
    assert record.app_version == APP_VERSION

    full_path = os.path.join(demo_file_settings.backup_dir, record.storage_path)
    with open(full_path, "rb") as backup_file:
        ciphertext = backup_file.read()
    archive = decrypt(demo_file_settings.backup_encryption_key, ciphertext)
    _dump, _docs, manifest = extract_archive(archive)
    assert manifest is not None
    assert manifest["app_version"] == APP_VERSION


def test_restore_refuses_newer_app_version(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    manifest = {"app_version": "999.0.0", "schema_revision": None}
    archive = build_archive(b"dump", b"", manifest=manifest)
    ciphertext = encrypt(demo_file_settings.backup_encryption_key, archive)

    with pytest.raises(backup_processing.BackupCompatibilityError, match="999.0.0"):
        backup_processing.restore_from_bytes(demo_file_engine, ciphertext, demo_file_settings)


def test_restore_refuses_unknown_schema_revision(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    manifest = {"app_version": APP_VERSION, "schema_revision": "9999_from_the_future"}
    archive = build_archive(b"dump", b"", manifest=manifest)
    ciphertext = encrypt(demo_file_settings.backup_encryption_key, archive)

    with pytest.raises(backup_processing.BackupCompatibilityError, match="9999_from_the_future"):
        backup_processing.restore_from_bytes(demo_file_engine, ciphertext, demo_file_settings)


def test_restore_round_trip_still_works_with_manifest(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    """The versioned archive a current backup produces restores cleanly (the
    manifest names this build's own version/revision → guard passes, no
    migration attempted)."""
    record = _run_backup(demo_file_engine, demo_file_settings)
    backup_processing.restore_backup(demo_file_engine, record.id, demo_file_settings)


def _database_dump_at_0092(tmp_path: Path) -> bytes:
    database_path = tmp_path / "archive-0092.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path}"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "-x",
            f"database_url={database_url}",
            "upgrade",
            PRE_RETENTION_REVISION,
        ],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    engine = create_engine(database_url)
    try:
        schema = inspect(engine)
        assert "backup_settings" not in schema.get_table_names()
        assert "backup_retention_events" not in schema.get_table_names()
        assert "prune_reason" not in {
            column["name"] for column in schema.get_columns("backup_jobs")
        }
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == PRE_RETENTION_REVISION
            )
    finally:
        engine.dispose()
    return database_path.read_bytes()


def _archive_from_0092(
    tmp_path: Path,
    settings: Settings,
    *,
    document_text: str = "archived documents",
) -> bytes:
    archived_documents = tmp_path / "archived-documents"
    archived_documents.mkdir()
    (archived_documents / "migration-restore.txt").write_text(document_text)
    return encrypt(
        settings.backup_encryption_key,
        build_archive(
            _database_dump_at_0092(tmp_path),
            backup_processing._tar_directory(str(archived_documents)),
            manifest={"app_version": APP_VERSION, "schema_revision": PRE_RETENTION_REVISION},
        ),
    )


def _assert_migration_failure_preserves_live_pair(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
    tmp_path: Path,
    monkeypatch,
    *,
    failure_kind: str,
) -> None:
    ciphertext = _archive_from_0092(tmp_path, demo_file_settings)

    before = repository.ensure_backup_local_destination_generation(
        demo_file_engine, demo_file_settings.backup_dir
    )
    updated = repository.update_backup_settings(
        demo_file_engine,
        {"frequency": "weekly", "local_min_free_bytes": 123},
        expected_updated_at=before.updated_at,
    )
    current = repository.activate_backup_retention(
        demo_file_engine,
        expected_updated_at=updated.updated_at,
    )
    live_document = Path(demo_file_settings.import_staging_dir) / "migration-restore.txt"
    live_document.parent.mkdir(parents=True, exist_ok=True)
    live_document.write_text("newer live documents")
    if failure_kind == "timeout":
        monkeypatch.setattr(
            backup_processing.subprocess,
            "run",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd=["alembic"], timeout=1)
            ),
        )
    else:
        monkeypatch.setattr(
            backup_processing.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=["alembic"],
                returncode=2,
                stdout="synthetic output must stay private",
                stderr="synthetic error must stay private",
            ),
        )

    with pytest.raises(backup_processing.BackupMigrationError) as raised:
        backup_processing.restore_from_bytes(demo_file_engine, ciphertext, demo_file_settings)

    assert raised.value.kind == failure_kind
    assert "synthetic" not in str(raised.value)
    assert live_document.read_text() == "newer live documents"
    restored = repository.get_backup_settings(demo_file_engine)
    assert restored.frequency == current.frequency
    assert restored.local_min_free_bytes == current.local_min_free_bytes
    assert restored.retention_review_required is True
    assert restored.retention_activated_at is None
    assert restored.local_destination_generation != current.local_destination_generation
    assert restored.offbox_destination_generation != current.offbox_destination_generation
    for generation in (
        restored.local_destination_generation,
        restored.offbox_destination_generation,
    ):
        events = repository.list_backup_retention_events_for_status(
            demo_file_engine,
            destination_generation=generation,
        )
        assert any(event.action == "restore_reset" for event in events)
    schema = inspect(demo_file_engine)
    assert "backup_settings" in schema.get_table_names()
    assert "backup_retention_events" in schema.get_table_names()
    assert "prune_reason" in {column["name"] for column in schema.get_columns("backup_jobs")}


def test_restore_migration_timeout_preserves_live_database_and_documents(
    demo_file_engine: Engine, demo_file_settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    _assert_migration_failure_preserves_live_pair(
        demo_file_engine,
        demo_file_settings,
        tmp_path,
        monkeypatch,
        failure_kind="timeout",
    )


def test_restore_migration_nonzero_preserves_live_database_and_documents(
    demo_file_engine: Engine, demo_file_settings: Settings, tmp_path: Path, monkeypatch
) -> None:
    _assert_migration_failure_preserves_live_pair(
        demo_file_engine,
        demo_file_settings,
        tmp_path,
        monkeypatch,
        failure_kind="nonzero_exit",
    )


def test_restore_migrates_real_0092_archive_before_live_promotion(
    demo_file_engine: Engine,
    demo_file_settings: Settings,
    tmp_path: Path,
) -> None:
    source = _run_backup(demo_file_engine, demo_file_settings)
    assert source.storage_path is not None
    ciphertext = _archive_from_0092(tmp_path, demo_file_settings)
    Path(demo_file_settings.backup_dir, source.storage_path).write_bytes(ciphertext)

    before = repository.get_backup_settings(demo_file_engine)
    current = repository.update_backup_settings(
        demo_file_engine,
        {"frequency": "weekly", "local_min_free_bytes": 123},
        expected_updated_at=before.updated_at,
    )
    live_directory = Path(demo_file_settings.import_staging_dir)
    live_directory.mkdir(parents=True, exist_ok=True)
    (live_directory / "newer-only.txt").write_text("must disappear")

    result = backup_processing.restore_backup(
        demo_file_engine,
        source.id,
        demo_file_settings,
    )

    with demo_file_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0094_backup_delete_intents"
        )
    assert result.job.id == source.id
    assert result.job.status == "completed"
    assert (live_directory / "migration-restore.txt").read_text() == "archived documents"
    assert not (live_directory / "newer-only.txt").exists()
    restored = repository.get_backup_settings(demo_file_engine)
    assert restored.frequency == "weekly"
    assert restored.local_min_free_bytes == 123
    assert restored.retention_review_required is True
    assert restored.retention_activated_at is None
    assert restored.local_destination_generation != current.local_destination_generation
    assert restored.offbox_destination_generation != current.offbox_destination_generation


def test_app_version_from_filename() -> None:
    parse = backup_processing._app_version_from_filename
    assert parse("abc123.v0.127.0.enc") == "0.127.0"
    assert parse("abc123.enc") is None
    assert parse("weird.vname.enc") is None
