from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from family_cfo_api import fixtures, models, repository
from family_cfo_api.backup_retention import RetentionMode, RetentionPolicy
from family_cfo_api.config import Settings
from family_cfo_api.db import create_database_engine


def _engine() -> Engine:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    fixtures.create_schema(engine)
    return engine


def _legacy_household(
    engine: Engine,
    household_id: str,
    *,
    updated_at: datetime,
    frequency: str = "daily",
    host: str | None = None,
    share: str | None = None,
    folder: str | None = None,
    username: str | None = None,
    password: str | None = None,
    domain: str | None = None,
    max_bytes: int | None = None,
    destination_path: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(models.households).values(
                id=household_id,
                display_name=f"Household {household_id}",
                base_currency="USD",
                backup_destination_path=destination_path,
                backup_frequency=frequency,
                backup_smb_host=host,
                backup_smb_share=share,
                backup_smb_folder=folder,
                backup_smb_username=username,
                backup_smb_password_encrypted=password,
                backup_smb_domain=domain,
                backup_max_bytes=max_bytes,
                created_at=updated_at,
                updated_at=updated_at,
            )
        )


def test_legacy_environment_parsing_is_bootstrap_safe(monkeypatch) -> None:
    monkeypatch.setenv("FAMILY_CFO_BACKUP_RETENTION_COUNT", "not-an-int")
    monkeypatch.setenv("FAMILY_CFO_OFFBOX_BACKUP_RETENTION_DAYS", "-1")

    settings = Settings.from_env()

    assert settings.backup_retention_count == 7
    assert settings.offbox_backup_retention_days == 0


def test_fresh_install_materializes_activated_defaults() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    try:
        record = repository.get_backup_settings(
            engine,
            settings=Settings(backup_retention_count=999, offbox_backup_retention_days=1),
            as_of=now,
            local_path_fingerprint="a" * 64,
        )

        assert record.key == "global"
        assert record.frequency == "daily"
        assert record.local_retention == RetentionPolicy(RetentionMode.TIERED, 3, 14, 90)
        assert record.offbox_retention == RetentionPolicy(RetentionMode.TIERED, 3, 14, 90)
        assert record.local_max_bytes is None
        assert record.offbox_max_bytes is None
        assert record.local_min_free_bytes == 1_073_741_824
        assert record.offbox_min_free_bytes == 1_073_741_824
        assert record.retention_review_required is False
        assert record.retention_activated_at == now
        assert record.local_path_fingerprint == "a" * 64
    finally:
        engine.dispose()


def test_concurrent_bootstrap_materializes_one_singleton(tmp_path) -> None:
    engine = create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'bootstrap.db'}")
    fixtures.create_schema(engine)
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = list(
                pool.map(
                    lambda _index: repository.get_backup_settings(
                        engine,
                        settings=Settings(),
                        as_of=now,
                    ),
                    range(8),
                )
            )
        assert {record.local_destination_generation for record in records} == {
            records[0].local_destination_generation
        }
        with engine.connect() as conn:
            assert conn.execute(
                select(func.count()).select_from(models.backup_settings)
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_upgrade_prefers_complete_smb_then_latest_and_marks_conflict() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    try:
        # Newer but incomplete must not beat a complete destination.
        _legacy_household(
            engine,
            "00000000-0000-0000-0000-000000000002",
            updated_at=now,
            frequency="weekly",
            host="newer-nas",
            share="backup",
            username="operator",
        )
        _legacy_household(
            engine,
            "00000000-0000-0000-0000-000000000001",
            updated_at=now - timedelta(days=1),
            frequency="hourly",
            host="chosen-nas",
            share="family-cfo",
            folder="archives",
            username="operator",
            password="encrypted-value",
            domain="WORKGROUP",
            max_bytes=123_456,
        )

        candidates = repository.list_backup_settings_legacy_candidates(engine)
        assert [candidate.household_id for candidate in candidates] == [
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ]

        record = repository.get_backup_settings(
            engine,
            settings=Settings(
                backup_retention_count=10_000,
                offbox_backup_retention_days=30,
            ),
            as_of=now,
        )
        assert record.smb_host == "chosen-nas"
        assert record.frequency == "hourly"
        assert record.local_weekly_until_days == 417
        assert record.offbox_retention == RetentionPolicy(RetentionMode.TIERED, 30, 30, 30)
        assert record.local_max_bytes == 123_456
        assert record.offbox_max_bytes == 123_456
        assert record.legacy_conflict_detected is True
        assert record.retention_review_required is True
        assert record.retention_activated_at is None
    finally:
        engine.dispose()


def test_legacy_tie_breaks_by_ascending_household_id() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    try:
        _legacy_household(
            engine,
            "00000000-0000-0000-0000-000000000002",
            updated_at=now,
            frequency="weekly",
        )
        _legacy_household(
            engine,
            "00000000-0000-0000-0000-000000000001",
            updated_at=now,
            frequency="hourly",
        )

        candidates = repository.list_backup_settings_legacy_candidates(engine)
        assert [candidate.household_id for candidate in candidates] == [
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ]
        record = repository.get_backup_settings(engine, settings=Settings(), as_of=now)
        assert record.frequency == "hourly"
        assert record.legacy_conflict_detected is True
    finally:
        engine.dispose()


def test_upgrade_off_and_old_history_extend_or_disable_local_horizon() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    oldest = now - timedelta(days=3_700)
    try:
        _legacy_household(
            engine,
            "00000000-0000-0000-0000-000000000001",
            updated_at=now,
            frequency="off",
        )
        with engine.begin() as conn:
            conn.execute(
                insert(models.backup_jobs).values(
                    id="10000000-0000-0000-0000-000000000001",
                    status="completed",
                    storage_path="old.enc",
                    size_bytes=10,
                    started_at=oldest,
                    completed_at=oldest + timedelta(minutes=1),
                    created_at=oldest,
                )
            )

        record = repository.get_backup_settings(
            engine,
            settings=Settings(
                backup_retention_count=-1,
                offbox_backup_retention_days=9_000,
            ),
            as_of=now,
        )
        assert record.frequency == "off"
        assert record.local_retention == RetentionPolicy(RetentionMode.KEEP_ALL)
        assert record.offbox_retention == RetentionPolicy(RetentionMode.KEEP_ALL)
        assert record.retention_review_required is True
    finally:
        engine.dispose()


def test_materialized_database_values_ignore_later_legacy_inputs() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    try:
        _legacy_household(
            engine,
            "00000000-0000-0000-0000-000000000001",
            updated_at=now,
            frequency="weekly",
        )
        first = repository.get_backup_settings(
            engine,
            settings=Settings(backup_retention_count=7, offbox_backup_retention_days=14),
            as_of=now,
        )
        second = repository.get_backup_settings(
            engine,
            settings=Settings(backup_retention_count=50_000, offbox_backup_retention_days=0),
            as_of=now + timedelta(days=1),
        )
        assert second == first
    finally:
        engine.dispose()


def test_update_cas_activation_and_generation_rotation(demo_engine: Engine) -> None:
    initial = repository.get_backup_settings(demo_engine)
    assert initial.retention_review_required is True

    tokenless = repository.update_backup_settings(
        demo_engine,
        {"frequency": "off", "smb_username": "new-user"},
        expected_updated_at=None,
    )
    assert tokenless.retention_review_required is True
    assert tokenless.retention_activated_at is None
    assert tokenless.offbox_destination_generation == initial.offbox_destination_generation

    with pytest.raises(repository.BackupSettingsValidationError):
        repository.update_backup_settings(
            demo_engine,
            {"local_keep_all_days": 2},
            expected_updated_at=None,
        )
    with pytest.raises(repository.BackupSettingsValidationError):
        repository.update_backup_settings(
            demo_engine,
            {"local_max_bytes": 10},
            expected_updated_at=None,
        )
    tokenless = repository.update_backup_settings(
        demo_engine,
        {"local_max_bytes": 10, "offbox_max_bytes": 10},
        expected_updated_at=None,
    )
    assert tokenless.local_max_bytes == tokenless.offbox_max_bytes == 10

    password_set = repository.update_backup_settings(
        demo_engine,
        {"smb_password_encrypted": "ciphertext"},
        expected_updated_at=tokenless.updated_at,
    )
    preserved = repository.update_backup_settings(
        demo_engine,
        {"smb_password_encrypted": None},
        expected_updated_at=password_set.updated_at,
    )
    assert preserved.smb_password_encrypted == "ciphertext"
    tokenless = repository.update_backup_settings(
        demo_engine,
        {"smb_password_encrypted": ""},
        expected_updated_at=preserved.updated_at,
    )
    assert tokenless.smb_password_encrypted is None

    changed_target = repository.update_backup_settings(
        demo_engine,
        {
            "smb_host": "nas.local",
            "local_max_bytes": 0,
            "local_retention_mode": "keep_all",
            "local_keep_all_days": None,
            "local_daily_until_days": None,
            "local_weekly_until_days": None,
            "local_path_fingerprint": "b" * 64,
        },
        expected_updated_at=tokenless.updated_at,
    )
    assert changed_target.local_max_bytes is None
    assert changed_target.offbox_destination_generation != tokenless.offbox_destination_generation
    assert changed_target.local_destination_generation != tokenless.local_destination_generation

    with pytest.raises(repository.BackupSettingsConflictError):
        repository.update_backup_settings(
            demo_engine,
            {"frequency": "daily"},
            expected_updated_at=tokenless.updated_at,
        )

    activated = repository.activate_backup_retention(
        demo_engine,
        expected_updated_at=changed_target.updated_at,
    )
    assert activated.retention_review_required is False
    assert activated.legacy_conflict_detected is False
    assert activated.retention_activated_at is not None

    with pytest.raises(repository.BackupSettingsConflictError):
        repository.activate_backup_retention(
            demo_engine,
            expected_updated_at=changed_target.updated_at,
        )

    rotated = repository.rotate_backup_destination_generation(
        demo_engine,
        "offbox",
        expected_updated_at=activated.updated_at,
    )
    assert rotated.updated_at > activated.updated_at
    assert rotated.offbox_destination_generation != activated.offbox_destination_generation
    with pytest.raises(repository.BackupSettingsConflictError):
        repository.rotate_backup_destination_generation(
            demo_engine,
            "offbox",
            expected_updated_at=activated.updated_at,
        )
    with pytest.raises(repository.BackupSettingsValidationError):
        repository.rotate_backup_destination_generation(
            demo_engine,
            "local",
            expected_updated_at=rotated.updated_at,
            local_path_fingerprint="not-a-sha256",
        )


def test_repository_normalizes_aware_instants_and_rejects_malformed_values() -> None:
    engine = _engine()
    eastern = timezone(timedelta(hours=-4))
    local_noon = datetime(2026, 9, 10, 12, tzinfo=eastern)
    try:
        initial = repository.get_backup_settings(
            engine,
            as_of=local_noon,
            local_path_fingerprint="a" * 64,
        )
        assert initial.created_at == datetime(2026, 9, 10, 16, tzinfo=UTC)
        assert initial.created_at.tzinfo is UTC

        for patch in (
            {"smb_password_encrypted": 1},
            {"local_path_fingerprint": "not-a-sha256"},
        ):
            with pytest.raises(repository.BackupSettingsValidationError):
                repository.update_backup_settings(
                    engine,
                    patch,
                    expected_updated_at=initial.updated_at,
                )
        with pytest.raises(repository.BackupSettingsValidationError, match="timezone-aware"):
            repository.update_backup_settings(
                engine,
                {"frequency": "off"},
                expected_updated_at=initial.updated_at.replace(tzinfo=None),
            )
    finally:
        engine.dispose()


def test_database_constraint_rejects_tiered_policy_with_null_horizon() -> None:
    engine = _engine()
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    try:
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                insert(models.backup_settings).values(
                    key="global",
                    frequency="daily",
                    local_retention_mode="tiered",
                    local_keep_all_days=None,
                    local_daily_until_days=14,
                    local_weekly_until_days=90,
                    offbox_retention_mode="keep_all",
                    offbox_keep_all_days=None,
                    offbox_daily_until_days=None,
                    offbox_weekly_until_days=None,
                    local_max_bytes=None,
                    offbox_max_bytes=None,
                    local_min_free_bytes=0,
                    offbox_min_free_bytes=0,
                    legacy_conflict_detected=False,
                    retention_review_required=False,
                    retention_activated_at=now,
                    local_destination_generation="10000000-0000-0000-0000-000000000001",
                    offbox_destination_generation="20000000-0000-0000-0000-000000000001",
                    created_at=now,
                    updated_at=now,
                )
            )
    finally:
        engine.dispose()


def test_repository_rejects_invalid_policy_before_database_constraint() -> None:
    engine = _engine()
    try:
        initial = repository.get_backup_settings(engine)
        with pytest.raises(repository.BackupSettingsValidationError):
            repository.update_backup_settings(
                engine,
                {"local_daily_until_days": 2},
                expected_updated_at=initial.updated_at,
            )
        with pytest.raises(repository.BackupSettingsValidationError):
            repository.update_backup_settings(
                engine,
                {"offbox_min_free_bytes": -1},
                expected_updated_at=initial.updated_at,
            )
    finally:
        engine.dispose()


def test_restore_reupsert_preserves_operational_values_and_rotates_generations(
    demo_engine: Engine,
) -> None:
    initial = repository.get_backup_settings(demo_engine)
    configured = repository.update_backup_settings(
        demo_engine,
        {"smb_host": "nas.local", "smb_password_encrypted": "ciphertext"},
        expected_updated_at=initial.updated_at,
    )
    with demo_engine.begin() as conn:
        conn.execute(
            update(models.backup_settings)
            .where(models.backup_settings.c.key == "global")
            .values(smb_host="restored-old-host", smb_password_encrypted="old-secret")
        )

    restored = repository.reupsert_backup_settings_after_restore(
        demo_engine,
        configured,
        restored_at=configured.updated_at + timedelta(seconds=1),
    )
    assert restored.smb_host == "nas.local"
    assert restored.smb_password_encrypted == "ciphertext"
    assert restored.local_destination_generation != configured.local_destination_generation
    assert restored.offbox_destination_generation != configured.offbox_destination_generation
    assert restored.retention_review_required is True
    assert restored.retention_activated_at is None


def test_retention_journal_is_idempotent_and_generation_scoped() -> None:
    engine = _engine()
    try:
        settings = repository.get_backup_settings(engine)
        first = repository.record_backup_retention_event(
            engine,
            destination="local",
            destination_generation=settings.local_destination_generation,
            operation_id="maintenance-1",
            action="inventory_succeeded",
            reason="inventory_ok",
        )
        duplicate = repository.record_backup_retention_event(
            engine,
            destination="local",
            destination_generation=settings.local_destination_generation,
            operation_id="maintenance-1",
            action="inventory_succeeded",
            reason="inventory_ok",
        )
        repository.record_backup_retention_event(
            engine,
            destination="local",
            destination_generation="00000000-0000-0000-0000-000000000000",
            operation_id="maintenance-1",
            action="inventory_succeeded",
            reason="inventory_ok",
        )

        assert duplicate.id == first.id
        events = repository.list_backup_retention_events_for_status(
            engine,
            destination_generation=settings.local_destination_generation,
        )
        assert [event.id for event in events] == [first.id]
        with pytest.raises(ValueError, match="non-negative integer"):
            repository.record_backup_retention_event(
                engine,
                destination="local",
                destination_generation=settings.local_destination_generation,
                operation_id="maintenance-invalid-size",
                action="anomaly_detected",
                reason="bad_size",
                size_bytes=True,
            )
        with pytest.raises(repository.BackupSettingsValidationError, match="timezone-aware"):
            repository.record_backup_retention_event(
                engine,
                destination="local",
                destination_generation=settings.local_destination_generation,
                operation_id="maintenance-naive-time",
                action="anomaly_detected",
                reason="bad_time",
                occurred_at=datetime(2026, 9, 10, 12, tzinfo=UTC).replace(tzinfo=None),
            )
        with engine.connect() as conn:
            assert conn.execute(
                select(func.count()).select_from(models.backup_retention_events)
            ).scalar_one() == 2
    finally:
        engine.dispose()


def test_inventory_order_and_prune_reason_keep_job_row() -> None:
    engine = _engine()
    taken_at = datetime(2026, 9, 10, 12, tzinfo=UTC)
    first_id = "00000000-0000-0000-0000-000000000001"
    second_id = "00000000-0000-0000-0000-000000000002"
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(models.backup_jobs),
                [
                    {
                        "id": second_id,
                        "status": "completed",
                        "storage_path": "second.enc",
                        "size_bytes": 20,
                        "started_at": taken_at,
                        "completed_at": taken_at,
                        "created_at": taken_at,
                    },
                    {
                        "id": first_id,
                        "status": "completed",
                        "storage_path": "first.enc",
                        "size_bytes": 10,
                        "started_at": taken_at,
                        "completed_at": taken_at,
                        "created_at": taken_at,
                    },
                ],
            )

        inventory = repository.list_completed_backup_jobs_for_inventory(engine)
        assert [job.id for job in inventory] == [first_id, second_id]

        repository.mark_backup_job_pruned(engine, first_id, reason="missing_file")
        pruned = repository.get_backup_job(engine, first_id)
        assert pruned is not None
        assert pruned.pruned_at is not None
        assert pruned.prune_reason == "missing_file"
        assert pruned.storage_path is None
        assert repository.get_backup_job(engine, second_id) is not None
    finally:
        engine.dispose()
