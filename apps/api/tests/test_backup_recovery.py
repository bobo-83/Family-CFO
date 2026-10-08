from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import insert
from sqlalchemy.engine import Engine

from family_cfo_api import __version__ as APP_VERSION
from family_cfo_api import backup_recovery, banksync, models, repository, smb_backup
from family_cfo_api.backup_retention import (
    BackupDestination,
    BackupInventoryItem,
    BackupTimestampSource,
    RetentionPolicy,
)


def _activate(engine: Engine) -> repository.BackupSettingsRecord:
    current = repository.get_backup_settings(engine)
    if current.retention_review_required:
        return repository.activate_backup_retention(engine, expected_revision=current.revision)
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


def _evidence(**changes: bool) -> repository.BackupRetentionStatusEvidence:
    values = {
        "latest_inventory_failed": False,
        "unresolved_prune_failure": False,
        "missing_deletion_timestamp": False,
        "target_opportunity": False,
        "target_shortened": False,
    }
    values.update(changes)
    return repository.BackupRetentionStatusEvidence(**values)


def _bulk_routine_events(
    engine: Engine, generation: str, as_of: datetime, *, pairs: int = 550
) -> None:
    rows = []
    for index in range(pairs):
        archive = f"old-{index}.enc"
        for action in ("delete_pending", "pruned"):
            sequence = len(rows)
            rows.append(
                {
                    "id": f"bulk-{sequence:08d}",
                    "destination": "local",
                    "archive_key": archive,
                    "operation_id": f"bulk-{index}",
                    "event_key": f"{sequence:064x}",
                    "destination_generation": generation,
                    "action": action,
                    "reason": "policy_expired",
                    "archive_taken_at": as_of - timedelta(days=20),
                    "occurred_at": as_of - timedelta(hours=1) + timedelta(seconds=index),
                }
            )
    first_scan_sequence = len(rows)
    rows.extend(
        {
            "id": f"scan-{index:08d}",
            "destination": "local",
            "archive_key": None,
            "operation_id": f"scan-{index}",
            "event_key": f"{index + first_scan_sequence:064x}",
            "destination_generation": generation,
            "action": "inventory_succeeded",
            "reason": "inventory_available",
            "archive_taken_at": None,
            "occurred_at": as_of - timedelta(minutes=10) + timedelta(milliseconds=index),
        }
        for index in range(600)
    )
    with engine.begin() as conn:
        conn.execute(insert(models.backup_retention_events), rows)


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
        "evidence": _evidence(),
        "plan": plan,
        "as_of": as_of,
    }
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=1),
        qualified=(newer,),
        **common,
    ) == "building"
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(newer,),
        **common,
    ) == "incomplete"
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(target,),
        **common,
    ) == "incomplete"
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(newer, target),
        **common,
    ) == "met"

    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(newer,),
        **{**common, "evidence": _evidence(target_opportunity=True, target_shortened=True)},
    ) == "shortened"
    assert backup_recovery._coverage(
        activated_at=as_of - timedelta(days=91),
        qualified=(newer,),
        **{**common, "evidence": _evidence(missing_deletion_timestamp=True)},
    ) == "unknown"


def test_status_event_lookup_starts_at_outer_bucket_boundary(
    demo_file_engine: Engine, monkeypatch
) -> None:
    as_of = datetime(2026, 9, 10, 12, tzinfo=UTC)
    policy = RetentionPolicy("tiered", 7, 30, 90)
    captured: dict[str, datetime | None] = {}

    def status_evidence(engine, *, destination_generation, since, target_start, target_end):
        captured["since"] = since
        captured["target_start"] = target_start
        captured["target_end"] = target_end
        assert destination_generation == "10000000-0000-0000-0000-000000000000"
        return _evidence()

    monkeypatch.setattr(repository, "backup_retention_status_evidence", status_evidence)
    evidence = backup_recovery._evidence(
        demo_file_engine,
        generation="10000000-0000-0000-0000-000000000000",
        policy=policy,
        retention_activated_at=as_of - timedelta(days=1),
        as_of=as_of,
    )

    assert evidence == _evidence()
    assert captured["since"] == datetime(2026, 6, 8, tzinfo=UTC)
    assert captured["target_start"] == as_of - timedelta(days=90)
    assert captured["target_end"] == datetime(2026, 6, 15, tzinfo=UTC)


def test_routine_journal_volume_does_not_make_qualified_history_unknown(
    demo_file_engine: Engine, demo_file_settings
) -> None:
    as_of = datetime(2026, 10, 8, 12, tzinfo=UTC)
    stored = _activate(demo_file_engine)
    _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=90) + timedelta(hours=1),
    )
    _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=1),
    )
    _bulk_routine_events(demo_file_engine, stored.local_destination_generation, as_of)

    snapshot = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )

    assert snapshot.local.coverage_status == "met"
    assert snapshot.local.status == "healthy"
    assert snapshot.local.reason_codes == ()


def test_target_deletion_and_missing_timestamp_survive_routine_journal_volume(
    demo_file_engine: Engine, demo_file_settings
) -> None:
    as_of = datetime(2026, 10, 8, 12, tzinfo=UTC)
    stored = _activate(demo_file_engine)
    _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=1),
    )
    target_time = as_of - timedelta(days=90) + timedelta(hours=1)
    repository.record_backup_retention_event(
        demo_file_engine,
        destination="local",
        destination_generation=stored.local_destination_generation,
        operation_id="target-delete",
        action="pruned",
        reason="policy_expired",
        archive_key="target.enc",
        archive_taken_at=target_time,
        occurred_at=as_of - timedelta(days=1),
    )
    _bulk_routine_events(demo_file_engine, stored.local_destination_generation, as_of)

    shortened = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )
    assert shortened.local.coverage_status == "shortened"
    assert shortened.local.status == "constrained"
    assert "coverage_shortened" in shortened.local.reason_codes

    repository.record_backup_retention_event(
        demo_file_engine,
        destination="local",
        destination_generation=stored.local_destination_generation,
        operation_id="unknown-delete",
        action="explicit_deleted",
        reason="explicit_delete",
        archive_key="unknown.enc",
        occurred_at=as_of - timedelta(days=2),
    )
    unknown = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )
    assert unknown.local.coverage_status == "unknown"
    assert unknown.local.status == "degraded"
    assert "coverage_unknown" in unknown.local.reason_codes


def test_status_evidence_scopes_generation_and_repairs_failures(
    demo_file_engine: Engine
) -> None:
    as_of = datetime(2026, 10, 8, 12, tzinfo=UTC)
    current = repository.get_backup_settings(demo_file_engine)
    generation = current.local_destination_generation
    old_generation = "00000000-0000-0000-0000-000000000001"
    repository.record_backup_retention_event(
        demo_file_engine,
        destination="local",
        destination_generation=old_generation,
        operation_id="old-failure",
        action="inventory_failed",
        reason="inventory_unavailable",
        occurred_at=as_of,
    )
    repository.record_backup_retention_event(
        demo_file_engine,
        destination="local",
        destination_generation=generation,
        operation_id="inventory-failure",
        action="inventory_failed",
        reason="inventory_unavailable",
        occurred_at=as_of - timedelta(minutes=2),
    )
    repository.record_backup_retention_event(
        demo_file_engine,
        destination="local",
        destination_generation=generation,
        operation_id="prune-failure",
        action="prune_failed",
        reason="delete_failed",
        archive_key="one.enc",
        occurred_at=as_of - timedelta(days=2),
    )
    _bulk_routine_events(demo_file_engine, generation, as_of)

    def evidence() -> repository.BackupRetentionStatusEvidence:
        return repository.backup_retention_status_evidence(
            demo_file_engine,
            destination_generation=generation,
            since=as_of - timedelta(days=90),
            target_start=as_of - timedelta(days=90),
            target_end=as_of - timedelta(days=83),
        )

    assert evidence().latest_inventory_failed is True
    assert evidence().unresolved_prune_failure is True
    repository.record_backup_retention_event(
        demo_file_engine,
        destination="local",
        destination_generation=generation,
        operation_id="inventory-repaired",
        action="inventory_succeeded",
        reason="inventory_available",
        occurred_at=as_of - timedelta(minutes=1),
    )
    repository.record_backup_retention_event(
        demo_file_engine,
        destination="local",
        destination_generation=generation,
        operation_id="prune-repaired",
        action="reconciled",
        reason="delete_failed",
        archive_key="one.enc",
        occurred_at=as_of - timedelta(minutes=1),
    )
    assert evidence().latest_inventory_failed is False
    assert evidence().unresolved_prune_failure is False


def test_status_evidence_uses_deterministic_ties_and_target_archive_time(
    demo_file_engine: Engine
) -> None:
    as_of = datetime(2026, 10, 8, 12, tzinfo=UTC)
    generation = repository.get_backup_settings(demo_file_engine).local_destination_generation
    target_start = as_of - timedelta(days=90)
    target_end = target_start + timedelta(days=7)

    def put(key: str, action: str, *, archive: str | None = None,
            taken_at: datetime | None = None, occurred_at: datetime = as_of) -> None:
        with demo_file_engine.begin() as conn:
            conn.execute(
                insert(models.backup_retention_events).values(
                    id=f"tie-{key[0]}",
                    destination="local",
                    archive_key=archive,
                    operation_id=f"tie-{key[0]}",
                    event_key=key,
                    destination_generation=generation,
                    action=action,
                    reason="test",
                    archive_taken_at=taken_at,
                    occurred_at=occurred_at,
                )
            )

    def evidence() -> repository.BackupRetentionStatusEvidence:
        return repository.backup_retention_status_evidence(
            demo_file_engine,
            destination_generation=generation,
            since=target_start,
            target_start=target_start,
            target_end=target_end,
        )

    put("b" * 64, "inventory_failed")
    put("c" * 64, "inventory_succeeded")
    put("f" * 64, "prune_failed", archive="tie.enc")
    put("g" * 64, "reconciled", archive="tie.enc")
    put("d" * 64, "delete_pending", archive="tie.enc")
    assert evidence().latest_inventory_failed is True
    # A newer retry intent is not a successful repair of the failed delete.
    assert evidence().unresolved_prune_failure is True
    put("a" * 64, "inventory_succeeded")
    put("e" * 64, "reconciled", archive="tie.enc")
    assert evidence().latest_inventory_failed is False
    assert evidence().unresolved_prune_failure is False

    # A target-time pending action is opportunity, not proof of shortening.
    put("h" * 64, "delete_pending", archive="target.enc",
        taken_at=target_start + timedelta(hours=1))
    assert evidence().target_opportunity is True
    assert evidence().target_shortened is False
    # Occurrence in the window does not substitute for an old archive time.
    put("i" * 64, "pruned", archive="outside.enc",
        taken_at=target_start - timedelta(hours=1))
    assert evidence().target_shortened is False
    # Capacity has no archive time; its occurrence time is the causal fallback.
    put("j" * 64, "capacity_blocked", occurred_at=target_start + timedelta(hours=2))
    assert evidence().target_shortened is True
    put("k" * 64, "prune_failed", archive="before-horizon.enc",
        occurred_at=target_start - timedelta(microseconds=1))
    assert evidence().unresolved_prune_failure is False
    put("l" * 64, "prune_failed", archive="at-horizon.enc", occurred_at=target_start)
    assert evidence().unresolved_prune_failure is True


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


def test_review_required_transitions_to_healthy_without_activation_pruning(
    demo_file_engine: Engine,
    demo_file_settings,
    monkeypatch,
) -> None:
    as_of = datetime(2026, 9, 10, 12, tzinfo=UTC)
    initial = repository.get_backup_settings(
        demo_file_engine, settings=demo_file_settings, as_of=as_of
    )
    assert initial.retention_review_required is True
    encrypted = banksync.encrypt_credential(demo_file_settings, "not-a-real-password")
    configured = repository.update_backup_settings(
        demo_file_engine,
        {
            "smb_host": "nas.invalid",
            "smb_share": "backups",
            "smb_username": "backup-user",
            "smb_password_encrypted": encrypted,
        },
        expected_revision=initial.revision,
    )
    local_job = _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=1),
    )
    remote_item = smb_backup.SmbInventoryItem(
        filename=f"{local_job.id}.v{APP_VERSION}.enc",
        job_id=local_job.id,
        app_version=APP_VERSION,
        size_bytes=local_job.size_bytes or 0,
        modified_at=int(local_job.started_at.timestamp()),
    )
    remote_inventory = smb_backup.SmbInventory((remote_item,), ())
    monkeypatch.setattr(smb_backup, "list_inventory", lambda target: remote_inventory)
    monkeypatch.setattr(
        smb_backup,
        "probe_inventory",
        lambda target, inventory: smb_backup.SmbReadProbeResult(
            "complete",
            1,
            1,
            remote_item,
            remote_item,
            (remote_item.filename,),
        ),
    )
    monkeypatch.setattr(
        smb_backup,
        "query_capacity",
        lambda target: smb_backup.SmbCapacity(
            20_000_000_000, 10_000_000_000, 10_000_000_000
        ),
    )

    before = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )
    assert before.local.status == "degraded"
    assert before.local.coverage_status == "unknown"
    assert set(before.local.reason_codes) >= {
        "retention_review_required",
        "coverage_unknown",
    }
    assert before.offbox.status == "degraded"
    assert before.offbox.coverage_status == "not_applicable"
    assert before.offbox.reason_codes == ("retention_review_required",)
    assert before.overall_status == "degraded"
    local_path = Path(demo_file_settings.backup_dir, local_job.storage_path or "")
    assert local_path.is_file()

    activated = repository.update_and_activate_backup_settings(
        demo_file_engine,
        {},
        expected_revision=configured.revision,
    )
    assert activated.revision != configured.revision
    assert local_path.is_file()

    after = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )
    assert after.local.status == "healthy"
    assert after.local.coverage_status == "building"
    assert after.offbox.status == "healthy"
    assert after.offbox.coverage_status == "not_applicable"
    assert after.overall_status == "healthy"
    assert local_path.is_file()


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


def test_unreadable_outer_target_cannot_report_met_or_healthy(
    demo_file_engine: Engine, demo_file_settings, monkeypatch
) -> None:
    as_of = datetime(2027, 1, 10, 12, tzinfo=UTC)
    current = _activate(demo_file_engine)
    encrypted = banksync.encrypt_credential(demo_file_settings, "not-a-real-password")
    repository.update_and_activate_backup_settings(
        demo_file_engine,
        {
            "smb_host": "nas.invalid",
            "smb_share": "backups",
            "smb_username": "backup-user",
            "smb_password_encrypted": encrypted,
            "offbox_retention_mode": "tiered",
            "offbox_keep_all_days": 7,
            "offbox_daily_until_days": 30,
            "offbox_weekly_until_days": 90,
        },
        expected_updated_at=current.updated_at,
    )
    target_job = _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=90) + timedelta(hours=1),
    )
    newer_job = _completed_local(
        demo_file_engine,
        demo_file_settings.backup_dir,
        started_at=as_of - timedelta(days=1),
    )
    remote = smb_backup.SmbInventory(
        tuple(
            smb_backup.SmbInventoryItem(
                filename=f"{job.id}.v{APP_VERSION}.enc",
                job_id=job.id,
                app_version=APP_VERSION,
                size_bytes=job.size_bytes or 0,
                modified_at=int(job.started_at.timestamp()),
            )
            for job in (newer_job, target_job)
        ),
        (),
    )
    monkeypatch.setattr(smb_backup, "list_inventory", lambda target: remote)

    def probe(target, inventory):
        newer = next(item for item in inventory.items if item.job_id == newer_job.id)
        target_item = next(item for item in inventory.items if item.job_id == target_job.id)
        assert target_item.filename != newer.filename
        # This is the bounded-probe edge: the oldest target fails, then the
        # newer readable archive supplies both readable endpoints.
        return smb_backup.SmbReadProbeResult(
            "complete",
            2,
            1,
            newer,
            newer,
            (newer.filename,),
        )

    monkeypatch.setattr(smb_backup, "probe_inventory", probe)
    monkeypatch.setattr(
        smb_backup,
        "query_capacity",
        lambda target: smb_backup.SmbCapacity(
            20_000_000_000, 10_000_000_000, 10_000_000_000
        ),
    )

    snapshot = backup_recovery.build_backup_recovery_snapshot(
        demo_file_engine, demo_file_settings, as_of=as_of
    )

    assert snapshot.offbox.probe_status == "complete"
    assert snapshot.offbox.probed_archive_count == 2
    assert snapshot.offbox.readable_archive_count == 1
    expected_newer = as_of - timedelta(days=1)
    assert snapshot.offbox.oldest_readable_at == expected_newer
    assert snapshot.offbox.newest_readable_at == expected_newer
    assert snapshot.offbox.coverage_status == "incomplete"
    assert snapshot.offbox.status == "degraded"
    assert "coverage_incomplete" in snapshot.offbox.reason_codes


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
            "complete",
            1,
            1,
            inventory.items[0],
            inventory.items[0],
            (inventory.items[0].filename,),
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
