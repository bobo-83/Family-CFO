import os

from sqlalchemy import delete
from sqlalchemy.engine import Engine

from family_cfo_api import backup_processing, fixtures, models, repository
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


def test_run_due_backups_runs_the_scheduled_wiring_and_respects_cadence(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    """M108/ADR 0019 regression guard: the scheduled backup pass must actually run
    (exercising the repository/smb/banksync wiring the old worker closure hid, where
    a bare `NameError` shipped) AND honour the cadence. The demo household defaults
    to a 'daily' schedule with no prior backup, so the first pass backs it up and an
    immediate second pass is skipped by the cadence gate."""
    ran = backup_processing.run_due_backups(demo_file_engine, demo_file_settings)
    assert ran == 1

    skipped = backup_processing.run_due_backups(demo_file_engine, demo_file_settings)
    assert skipped == 0  # a backup just completed → within the daily window


def test_run_backup_once_creates_completed_encrypted_archive(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    record = _run_backup(demo_file_engine, demo_file_settings)

    assert record.status == "completed"
    assert record.storage_path is not None
    assert record.size_bytes is not None and record.size_bytes > 0

    full_path = os.path.join(demo_file_settings.backup_dir, record.storage_path)
    assert os.path.exists(full_path)

    with open(full_path, "rb") as backup_file:
        ciphertext = backup_file.read()
    # An encrypted archive should not contain the plaintext household name.
    assert fixtures.DEMO_HOUSEHOLD_ID.encode() not in ciphertext


def test_run_backup_once_without_encryption_key_fails(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    broken_settings = Settings(
        database_url=demo_file_settings.database_url,
        import_staging_dir=demo_file_settings.import_staging_dir,
        backup_dir=demo_file_settings.backup_dir,
        backup_encryption_key=None,
    )

    record = _run_backup(demo_file_engine, broken_settings)

    assert record.status == "failed"
    assert record.error_message is not None
    assert (
        "BACKUP_ENCRYPTION_KEY" in record.error_message
        or "BackupConfigurationError" in record.error_message
    )


def test_run_backup_once_rejects_in_memory_sqlite(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    broken_settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        import_staging_dir=demo_file_settings.import_staging_dir,
        backup_dir=demo_file_settings.backup_dir,
        backup_encryption_key=demo_file_settings.backup_encryption_key,
    )

    record = _run_backup(demo_file_engine, broken_settings)

    assert record.status == "failed"
    assert "BackupConfigurationError" in record.error_message


def test_restore_backup_recovers_deleted_household(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    record = _run_backup(demo_file_engine, demo_file_settings)
    assert record.status == "completed"

    with demo_file_engine.begin() as conn:
        conn.execute(delete(models.household_memberships))
        conn.execute(delete(models.users))
        conn.execute(delete(models.households))

    assert repository.get_household(demo_file_engine, fixtures.DEMO_HOUSEHOLD_ID) is None

    backup_processing.restore_backup(
        demo_file_engine,
        record.id,
        database_url=demo_file_settings.database_url,
        staging_dir=demo_file_settings.import_staging_dir,
        backup_dir=demo_file_settings.backup_dir,
        encryption_key=demo_file_settings.backup_encryption_key,
    )

    restored = repository.get_household(demo_file_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert restored is not None
    assert restored.base_currency == "USD"


def test_restore_backup_recovers_staged_documents(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    os.makedirs(demo_file_settings.import_staging_dir, exist_ok=True)
    staged_file = os.path.join(demo_file_settings.import_staging_dir, "receipt.txt")
    with open(staged_file, "w") as handle:
        handle.write("synthetic receipt contents")

    record = _run_backup(demo_file_engine, demo_file_settings)
    assert record.status == "completed"

    os.remove(staged_file)
    assert not os.path.exists(staged_file)

    backup_processing.restore_backup(
        demo_file_engine,
        record.id,
        database_url=demo_file_settings.database_url,
        staging_dir=demo_file_settings.import_staging_dir,
        backup_dir=demo_file_settings.backup_dir,
        encryption_key=demo_file_settings.backup_encryption_key,
    )

    assert os.path.exists(staged_file)
    with open(staged_file) as handle:
        assert handle.read() == "synthetic receipt contents"


def test_restore_backup_recovers_transaction_attachments(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    """M100/M108: a check/receipt image attached to a transaction is stored under the
    staging dir's `attachments/` subfolder. It must survive a backup→restore roundtrip
    — the whole staging tree is archived — so this guards the check-image case
    explicitly (the other test only covers a top-level staged file)."""
    attach_dir = os.path.join(demo_file_settings.import_staging_dir, "attachments")
    os.makedirs(attach_dir, exist_ok=True)
    image_path = os.path.join(attach_dir, "txn-probe.jpg")
    image_bytes = b"\xff\xd8\xff-synthetic-check-image"
    with open(image_path, "wb") as handle:
        handle.write(image_bytes)

    record = _run_backup(demo_file_engine, demo_file_settings)
    assert record.status == "completed"

    os.remove(image_path)
    assert not os.path.exists(image_path)

    backup_processing.restore_backup(
        demo_file_engine,
        record.id,
        database_url=demo_file_settings.database_url,
        staging_dir=demo_file_settings.import_staging_dir,
        backup_dir=demo_file_settings.backup_dir,
        encryption_key=demo_file_settings.backup_encryption_key,
    )

    assert os.path.exists(image_path)
    with open(image_path, "rb") as handle:
        assert handle.read() == image_bytes


def test_restore_backup_wrong_key_raises(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    record = _run_backup(demo_file_engine, demo_file_settings)

    try:
        backup_processing.restore_backup(
            demo_file_engine,
            record.id,
            database_url=demo_file_settings.database_url,
            staging_dir=demo_file_settings.import_staging_dir,
            backup_dir=demo_file_settings.backup_dir,
            encryption_key="wrongkeywrongkeywrongkeywrongkeywrongkeyAAA=",
        )
        raise AssertionError("expected BackupEncryptionError")
    except Exception as exc:  # noqa: BLE001 - asserting the specific failure mode below
        assert type(exc).__name__ == "BackupEncryptionError"


def test_retention_prunes_oldest_backups_beyond_count(
    demo_file_engine: Engine, demo_file_settings: Settings
) -> None:
    limited_settings = Settings(
        database_url=demo_file_settings.database_url,
        import_staging_dir=demo_file_settings.import_staging_dir,
        backup_dir=demo_file_settings.backup_dir,
        backup_encryption_key=demo_file_settings.backup_encryption_key,
        backup_retention_count=2,
    )

    records = [_run_backup(demo_file_engine, limited_settings) for _ in range(4)]

    all_jobs = repository.list_backup_jobs(demo_file_engine)
    pruned = [job for job in all_jobs if job.pruned_at is not None]
    not_pruned = [job for job in all_jobs if job.pruned_at is None]

    assert len(pruned) == 2
    assert len(not_pruned) == 2
    assert {job.id for job in pruned} == {records[0].id, records[1].id}
    for job in pruned:
        assert job.storage_path is None
