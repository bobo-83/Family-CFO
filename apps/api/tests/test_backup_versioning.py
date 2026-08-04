"""Backup version labels + restore-compatibility guard.

Every archive seals a manifest (app_version, schema_revision) inside the
encrypted tar; the job row records the same pair so the UI can label each
backup. Restoring an archive from a NEWER app than the box runs is refused
(there is no schema downgrade path); an older-but-known archive restores and
is migrated forward; a manifest-less legacy archive restores as-is.
"""

import os

import pytest
from family_cfo_backup import build_archive, decrypt, encrypt, extract_archive
from sqlalchemy.engine import Engine

from family_cfo_api import __version__ as APP_VERSION
from family_cfo_api import backup_processing, repository
from family_cfo_api.config import Settings


def _run_backup(engine: Engine, settings: Settings) -> repository.BackupJobRecord:
    backup_job_id = backup_processing.run_backup_once(
        engine,
        database_url=settings.database_url,
        staging_dir=settings.import_staging_dir,
        backup_dir=settings.backup_dir,
        encryption_key=settings.backup_encryption_key,
        retention_count=settings.backup_retention_count,
    )
    record = repository.get_backup_job(engine, backup_job_id)
    assert record is not None
    return record


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


def test_restore_refuses_newer_app_version(demo_file_settings: Settings) -> None:
    manifest = {"app_version": "999.0.0", "schema_revision": None}
    archive = build_archive(b"dump", b"", manifest=manifest)
    ciphertext = encrypt(demo_file_settings.backup_encryption_key, archive)

    with pytest.raises(backup_processing.BackupCompatibilityError, match="999.0.0"):
        backup_processing.restore_from_bytes(
            ciphertext,
            database_url=demo_file_settings.database_url,
            staging_dir=demo_file_settings.import_staging_dir,
            encryption_key=demo_file_settings.backup_encryption_key,
        )


def test_restore_refuses_unknown_schema_revision(demo_file_settings: Settings) -> None:
    manifest = {"app_version": APP_VERSION, "schema_revision": "9999_from_the_future"}
    archive = build_archive(b"dump", b"", manifest=manifest)
    ciphertext = encrypt(demo_file_settings.backup_encryption_key, archive)

    with pytest.raises(backup_processing.BackupCompatibilityError, match="9999_from_the_future"):
        backup_processing.restore_from_bytes(
            ciphertext,
            database_url=demo_file_settings.database_url,
            staging_dir=demo_file_settings.import_staging_dir,
            encryption_key=demo_file_settings.backup_encryption_key,
        )


def test_restore_round_trip_still_works_with_manifest(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    """The versioned archive a current backup produces restores cleanly (the
    manifest names this build's own version/revision → guard passes, no
    migration attempted)."""
    record = _run_backup(demo_file_engine, demo_file_settings)
    backup_processing.restore_backup(
        demo_file_engine,
        record.id,
        database_url=demo_file_settings.database_url,
        staging_dir=demo_file_settings.import_staging_dir,
        backup_dir=demo_file_settings.backup_dir,
        encryption_key=demo_file_settings.backup_encryption_key,
    )


def test_app_version_from_filename() -> None:
    parse = backup_processing._app_version_from_filename
    assert parse("abc123.v0.127.0.enc") == "0.127.0"
    assert parse("abc123.enc") is None
    assert parse("weird.vname.enc") is None
