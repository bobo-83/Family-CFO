from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import Engine

from family_cfo_api import __version__ as APP_VERSION
from family_cfo_api import backup_recovery, banksync, repository, smb_backup
from family_cfo_api.backup_retention import (
    BackupDestination,
    BackupInventoryItem,
    BackupTimestampSource,
    RetentionPolicy,
)


def _activate(engine: Engine) -> repository.BackupSettingsRecord:
    current = repository.get_backup_settings(engine)
    if current.retention_review_required:
        return repository.activate_backup_retention(engine, expected_updated_at=current.updated_at)
    return current


def _completed_local(
    engine: Engine,
    backup_dir: str,
    *,
    started_at: datetime,
    size: int = 16,
) -> repository.BackupJobRecord:
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    job = repository.create_backup_job(engine, started_at=started_at)
    repository.update_backup_job(engine, job.id, status="running")
    filename = f"{job.id}.enc"
    Path(backup_dir, filename).write_bytes(b"x" * size)
    repository.complete_backup_job_local(
        engine,
        job.id,
        storage_path=filename,
        size_bytes=size,
        remote_status="skipped",
        app_version=APP_VERSION,
        schema_revision=None,
    )
    completed = repository.get_backup_job(engine, job.id)
    assert completed is not None
    return completed


def _inventory_item(key: str, taken_at: datetime) -> BackupInventoryItem:
    return BackupInventoryItem(
        destination=BackupDestination.LOCAL,
        archive_key=key,
        taken_at=taken_at,
        timestamp_source=BackupTimestampSource.JOB_STARTED_AT,
        size_bytes=10,
    )


def _retention_event(
    *,
    action: str,
    occurred_at: datetime,
    archive_taken_at: datetime | None,
) -> repository.BackupRetentionEventRecord:
    return repository.BackupRetentionEventRecord(
        id=f"event-{action}",
        destination="local",
        archive_key="old.enc",
        backup_job_id=None,
        operation_id=f"operation-{action}",
        event_key=f"event-key-{action}",
        destination_generation="10000000-0000-0000-0000-000000000000",
        action=action,
        reason="test",
        archive_taken_at=archive_taken_at,
        timestamp_source="job_started_at" if archive_taken_at else None,
        size_bytes=10 if archive_taken_at else None,
        policy_updated_at=None,
        policy_snapshot=None,
        detail=None,
        occurred_at=occurred_at,
    )


def test_outer_bucket_and_coverage_precedence_are_deterministic() -> None:
    as_of = datetime(2026, 9, 10, 12, tzinfo=UTC)
    policy = RetentionPolicy("tiered", 7, 30, 90)
    cutoff, bucket_start, bucket_end = backup_recovery._target_interval(policy, as_of)
    assert cutoff == as_of - timedelta(days=90)
    assert bucket_start == datetime(2026, 6, 8, tzinfo=UTC)
    assert bucket_end == datetime(2026, 6, 15, tzinfo=UTC)

    newer = _inventory_item("new.enc", as_of - timedelta(days=1))
    target = _inventory_item("target.enc", cutoff + timedelta(hours=1))
    plan = backup_recovery._plan((newer, target), policy=policy, max_bytes=None, as_of=as_of)
    common = {
        "policy": policy,
        "review_required": False,
        "probe_complete": True,
        "protected_count": 0,
        "events_incomplete": False,
        "plan": plan,
        "as_of": as_of,
    }
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=1),
        qualified=(newer,),
        events=(),
        **common,
    ) == "building"
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(newer,),
        events=(),
        **common,
    ) == "incomplete"
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(target,),
        events=(),
        **common,
    ) == "incomplete"
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(newer, target),
        events=(),
        **common,
    ) == "met"

    shortened = _retention_event(
        action="explicit_deleted",
        occurred_at=as_of,
        archive_taken_at=cutoff + timedelta(hours=2),
    )
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(newer,),
        events=(shortened,),
        **common,
    ) == "shortened"
    missing_causality = _retention_event(
        action="explicit_deleted", occurred_at=as_of, archive_taken_at=None
    )
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(newer,),
        events=(missing_causality,),
        **common,
    ) == "unknown"


def test_status_event_lookup_starts_at_outer_bucket_boundary(
    demo_file_engine: Engine, monkeypatch
) -> None:
    as_of = datetime(2026, 9, 10, 12, tzinfo=UTC)
    policy = RetentionPolicy("tiered", 7, 30, 90)
    captured: dict[str, datetime | None] = {}

    def list_events(engine, *, destination_generation, since, limit):
        captured["since"] = since
        assert destination_generation == "10000000-0000-0000-0000-000000000000"
        assert limit == 501
        return []

    monkeypatch.setattr(repository, "list_backup_retention_events_for_status", list_events)
    events, incomplete = backup_recovery._events(
        demo_file_engine,
        generation="10000000-0000-0000-0000-000000000000",
        policy=policy,
        retention_activated_at=as_of - timedelta(days=1),
        as_of=as_of,
    )

    assert events == ()
    assert incomplete is False
    assert captured["since"] == datetime(2026, 6, 8, tzinfo=UTC)


def test_local_status_meets_outer_bucket_with_a_newer_candidate(
    demo_file_engine: Engine, demo_file_settings
) -> None:
    as_of = datetime(2026, 9, 10, 12, tzinfo=UTC)
    _activate(demo_file_engine)
    _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=90),
    )
    _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=1),
    )

    snapshot = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )

    assert snapshot.local.coverage_status == "met"
    assert snapshot.local.status == "healthy"
    assert snapshot.local.oldest_readable_at == as_of - timedelta(days=90)
    assert snapshot.local.newest_readable_at == as_of - timedelta(days=1)
    assert snapshot.local.readable_archive_count == 2
    assert snapshot.offbox.status == "not_configured"
    assert snapshot.overall_status == "healthy"


def test_protected_local_evidence_makes_coverage_unknown_without_mutation(
    demo_file_engine: Engine, demo_file_settings
) -> None:
    as_of = datetime(2026, 9, 10, 12, tzinfo=UTC)
    _activate(demo_file_engine)
    _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=1),
    )
    orphan = Path(demo_file_settings.backup_dir, "orphan.enc")
    orphan.write_bytes(b"protected")

    snapshot = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )

    assert snapshot.local.status == "degraded"
    assert snapshot.local.coverage_status == "unknown"
    assert snapshot.local.protected_anomaly_count == 1
    assert orphan.read_bytes() == b"protected"
    assert (
        repository.list_backup_retention_events_for_status(
            demo_file_engine,
            destination_generation=repository.get_backup_settings(
                demo_file_engine
            ).local_destination_generation,
        )
        == []
    )


def test_newest_invariant_reports_unsatisfied_logical_cap(
    demo_file_engine: Engine, demo_file_settings
) -> None:
    as_of = datetime(2026, 9, 10, 12, tzinfo=UTC)
    current = _activate(demo_file_engine)
    current = repository.update_backup_settings(
        demo_file_engine,
        {"local_max_bytes": 5},
        expected_updated_at=current.updated_at,
    )
    _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=2),
        size=10,
    )
    _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=1),
        size=10,
    )

    snapshot = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )

    assert snapshot.local.status == "constrained"
    assert "logical_cap_unsatisfied" in snapshot.local.reason_codes
    assert snapshot.local.pending_prune_count == 1
    assert snapshot.local.pending_prune_bytes == 10
    assert repository.get_backup_settings(demo_file_engine).updated_at == current.updated_at


def test_remote_status_preserves_unavailable_and_timestamp_fallback_states(
    demo_file_engine: Engine, demo_file_settings, monkeypatch
) -> None:
    as_of = datetime(2026, 9, 10, 12, tzinfo=UTC)
    current = _activate(demo_file_engine)
    encrypted = banksync.encrypt_credential(demo_file_settings, "not-a-real-password")
    repository.update_backup_settings(
        demo_file_engine,
        {
            "smb_host": "nas.invalid",
            "smb_share": "backups",
            "smb_username": "backup-user",
            "smb_password_encrypted": encrypted,
        },
        expected_updated_at=current.updated_at,
    )
    Path(demo_file_settings.backup_dir).mkdir(parents=True, exist_ok=True)
    job_id = "10000000-0000-0000-0000-000000000001"
    remote = smb_backup.SmbInventory(
        (
            smb_backup.SmbInventoryItem(
                filename=f"{job_id}.v{APP_VERSION}.enc",
                job_id=job_id,
                app_version=APP_VERSION,
                size_bytes=20,
                modified_at=int((as_of - timedelta(days=5)).timestamp()),
            ),
        ),
        (),
    )
    monkeypatch.setattr(smb_backup, "list_inventory", lambda target: remote)
    monkeypatch.setattr(
        smb_backup,
        "probe_inventory",
        lambda target, inventory: smb_backup.SmbReadProbeResult(
            "complete", 1, 1, inventory.items[0], inventory.items[0]
        ),
    )
    monkeypatch.setattr(
        smb_backup,
        "query_capacity",
        lambda target: smb_backup.SmbCapacity(
            20_000_000_000, 10_000_000_000, 10_000_000_000
        ),
    )

    fallback = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )
    assert fallback.offbox.oldest_timestamp_source == "remote_modified_at"
    assert fallback.offbox.status == "degraded"
    assert "remote_timestamp_fallback" in fallback.offbox.reason_codes

    monkeypatch.setattr(
        smb_backup,
        "list_inventory",
        lambda target: (_ for _ in ()).throw(
            smb_backup.SmbInventoryError(
                "destination_unreachable", "The Synology inventory is unavailable."
            )
        ),
    )
    unavailable = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )
    assert unavailable.offbox.status == "unavailable"
    assert unavailable.offbox.readable_archive_count is None
    assert unavailable.overall_status == "degraded"
