from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
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
from sqlalchemy import text
from sqlalchemy.engine import Engine

from family_cfo_api import __version__ as APP_VERSION
from family_cfo_api import banksync, repository, smb_backup
from family_cfo_api.backup_operation_lock import (
    BackupOperationBusyError,
    BackupOperationLease,
    BackupOperationLockLostError,
    acquire_backup_operation_lock,
    try_acquire_backup_operation_lock,
)
from family_cfo_api.backup_retention import (
    BackupDestination,
    BackupInventoryItem,
    BackupTimestampSource,
    RetentionAction,
    RetentionDecision,
    RetentionPlan,
    RetentionPolicy,
    RetentionReason,
    plan_retention,
)
from family_cfo_api.backup_storage import (
    CapacityObservation,
    LocalInventory,
    UnsafeBackupPathError,
    atomic_promote_local_archive,
    capacity_observation,
    collect_local_inventory,
    estimate_next_backup_bytes,
    query_local_capacity,
    resolve_managed_local_path,
)
from family_cfo_api.config import Settings

logger = logging.getLogger(__name__)

BACKUP_CADENCE_MINUTES = {
    "every_15min": 15,
    "hourly": 60,
    "every_6h": 360,
    "daily": 1440,
    "weekly": 10080,
}
_STALE_PARTIAL_AGE = timedelta(hours=24)
_WORKER_INTERVAL = timedelta(minutes=5)


class BackupCompatibilityError(ValueError):
    """The archive requires a newer application or database revision."""


class BackupConfigurationError(ValueError):
    """Backup process configuration cannot safely execute."""


@dataclass(frozen=True, slots=True)
class BackupExecutionConfig:
    database_url: str
    staging_dir: str
    backup_dir: str
    encryption_key: str | None
    io_timeout_seconds: int
    frequency: str
    smb_target: smb_backup.SmbTarget | None
    local_policy: RetentionPolicy
    offbox_policy: RetentionPolicy
    local_max_bytes: int | None
    offbox_max_bytes: int | None
    local_min_free_bytes: int
    offbox_min_free_bytes: int
    retention_review_required: bool
    retention_activated_at: datetime | None
    settings_updated_at: datetime
    local_destination_generation: str
    offbox_destination_generation: str
    local_path_fingerprint: str

    @property
    def retention_active(self) -> bool:
        return not self.retention_review_required and self.retention_activated_at is not None


@dataclass(frozen=True, slots=True)
class BackupMaintenanceResult:
    lock_skipped: bool = False
    reconciled_jobs: int = 0
    interrupted_jobs: int = 0
    local_partials_removed: int = 0
    remote_partials_removed: int = 0
    local_pruned: int = 0
    remote_pruned: int = 0


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def build_backup_execution_config(engine: Engine, settings: Settings) -> BackupExecutionConfig:
    if settings.backup_io_timeout_seconds <= 0:
        raise BackupConfigurationError("backup I/O timeout must be positive")
    try:
        stored = repository.ensure_backup_local_destination_generation(engine, settings.backup_dir)
    except repository.BackupSettingsConflictError:
        # A concurrent settings save won the compare-and-swap; rebuild from its
        # authoritative revision rather than mixing two configuration snapshots.
        stored = repository.ensure_backup_local_destination_generation(engine, settings.backup_dir)
    target: smb_backup.SmbTarget | None = None
    configured = all(
        (
            stored.smb_host,
            stored.smb_share,
            stored.smb_username,
            stored.smb_password_encrypted,
        )
    )
    if configured:
        assert stored.smb_host is not None
        assert stored.smb_share is not None
        assert stored.smb_username is not None
        assert stored.smb_password_encrypted is not None
        try:
            password = banksync.decrypt_credential(settings, stored.smb_password_encrypted)
        except Exception as exc:  # secret errors must remain redacted
            raise BackupConfigurationError("stored SMB credential could not be opened") from exc
        target = smb_backup.SmbTarget(
            host=stored.smb_host,
            share=stored.smb_share,
            folder=stored.smb_folder,
            username=stored.smb_username,
            password=password,
            domain=stored.smb_domain,
            io_timeout_seconds=settings.backup_io_timeout_seconds,
        )
    return BackupExecutionConfig(
        database_url=settings.database_url,
        staging_dir=settings.import_staging_dir,
        backup_dir=settings.backup_dir,
        encryption_key=settings.backup_encryption_key,
        io_timeout_seconds=settings.backup_io_timeout_seconds,
        frequency=stored.frequency,
        smb_target=target,
        local_policy=stored.local_retention,
        offbox_policy=stored.offbox_retention,
        local_max_bytes=stored.local_max_bytes,
        offbox_max_bytes=stored.offbox_max_bytes,
        local_min_free_bytes=stored.local_min_free_bytes,
        offbox_min_free_bytes=stored.offbox_min_free_bytes,
        retention_review_required=stored.retention_review_required,
        retention_activated_at=stored.retention_activated_at,
        settings_updated_at=stored.updated_at,
        local_destination_generation=stored.local_destination_generation,
        offbox_destination_generation=stored.offbox_destination_generation,
        local_path_fingerprint=stored.local_path_fingerprint
        or repository.backup_local_path_fingerprint(settings.backup_dir),
    )


def run_due_backups(engine: Engine, settings: Settings, *, now: datetime | None = None) -> int:
    """Make one box-global cadence decision and start at most one backup."""
    as_of = _as_utc(now or datetime.now(UTC))
    config = build_backup_execution_config(engine, settings)
    if config.frequency == "off":
        return 0
    minutes = BACKUP_CADENCE_MINUTES.get(config.frequency, 1440)
    latest = repository.latest_completed_backup_job(engine)
    if latest is not None and latest.completed_at is not None:
        cutoff = as_of - timedelta(minutes=max(1, minutes - 2))
        if _as_utc(latest.completed_at) >= cutoff:
            return 0
    try:
        run_backup_once(engine, config, now=as_of)
    except BackupOperationBusyError:
        _record_lock_skipped(engine, config, operation_id=str(uuid.uuid4()), as_of=as_of)
        return 0
    return 1


def select_backup_adapter(database_url: str, *, timeout_seconds: int = 3600) -> BackupAdapter:
    scheme = database_url.split("://", 1)[0].split("+")[0]
    if scheme == "postgresql":
        return PgDumpBackupAdapter(database_url, timeout_seconds=timeout_seconds)
    if scheme == "sqlite":
        path = database_url.split("///", 1)[-1] if "///" in database_url else ""
        if not path or path == ":memory:":
            raise BackupConfigurationError(
                "backups require a file-based sqlite database_url in this environment"
            )
        return SqliteFileBackupAdapter(Path(path))
    raise BackupConfigurationError(f"no backup adapter for database scheme {scheme!r}")


def _policy_snapshot(
    policy: RetentionPolicy, max_bytes: int | None, reserve_bytes: int
) -> dict[str, object]:
    return {
        "mode": policy.mode.value,
        "keep_all_days": policy.keep_all_days,
        "daily_until_days": policy.daily_until_days,
        "weekly_until_days": policy.weekly_until_days,
        "max_bytes": max_bytes,
        "reserve_bytes": reserve_bytes,
    }


def _journal(
    engine: Engine,
    config: BackupExecutionConfig,
    *,
    operation_id: str,
    destination: str,
    action: str,
    reason: str,
    item: BackupInventoryItem | None = None,
    archive_key: str | None = None,
    backup_job_id: str | None = None,
    detail: str | None = None,
    occurred_at: datetime | None = None,
) -> None:
    policy = config.local_policy if destination == "local" else config.offbox_policy
    maximum = config.local_max_bytes if destination == "local" else config.offbox_max_bytes
    reserve = (
        config.local_min_free_bytes if destination == "local" else config.offbox_min_free_bytes
    )
    generation = (
        config.local_destination_generation
        if destination == "local"
        else config.offbox_destination_generation
    )
    try:
        repository.record_backup_retention_event(
            engine,
            destination=destination,
            destination_generation=generation,
            operation_id=operation_id,
            action=action,
            reason=reason,
            archive_key=item.archive_key if item is not None else archive_key,
            backup_job_id=item.job_id if item is not None else backup_job_id,
            archive_taken_at=item.taken_at if item is not None else None,
            timestamp_source=(item.timestamp_source.value if item is not None else None),
            size_bytes=item.size_bytes if item is not None else None,
            policy_updated_at=config.settings_updated_at,
            policy_snapshot=_policy_snapshot(policy, maximum, reserve),
            detail=detail,
            occurred_at=occurred_at,
        )
    except Exception as exc:  # noqa: BLE001 - journal failure must not affect archives
        logger.warning(
            "backup journal write failed action=%s reason=%s error_type=%s",
            action,
            reason,
            type(exc).__name__,
        )


def _record_lock_skipped(
    engine: Engine,
    config: BackupExecutionConfig,
    *,
    operation_id: str,
    as_of: datetime,
) -> None:
    _journal(
        engine,
        config,
        operation_id=operation_id,
        destination="local",
        action="lock_skipped",
        reason="backup_in_progress",
        occurred_at=as_of,
    )
    if config.smb_target is not None:
        _journal(
            engine,
            config,
            operation_id=operation_id,
            destination="offbox",
            action="lock_skipped",
            reason="backup_in_progress",
            occurred_at=as_of,
        )


def _retention_plan(
    *,
    as_of: datetime,
    inventory: tuple[BackupInventoryItem, ...],
    policy: RetentionPolicy,
    max_bytes: int | None,
    incoming_bytes: int | None = None,
) -> RetentionPlan:
    effective_max = max_bytes
    if max_bytes is not None and incoming_bytes is not None:
        effective_max = max(1, max_bytes - incoming_bytes)
    return plan_retention(
        as_of=as_of,
        inventory=inventory,
        policy=policy,
        max_bytes=effective_max,
    )


def _prune_reason(decision: RetentionDecision) -> str:
    if decision.reason is RetentionReason.EXPIRED:
        return "policy_expired"
    if decision.reason is RetentionReason.BUCKET_SUPERSEDED:
        return "bucket_superseded"
    return "max_bytes"


def _reconcile_missing_local(
    engine: Engine,
    config: BackupExecutionConfig,
    inventory: LocalInventory,
    lease: BackupOperationLease,
    *,
    operation_id: str,
) -> int:
    reconciled = 0
    for entry in inventory.entries:
        if not {"missing_file", "missing_storage_path"}.intersection(entry.item.anomaly_codes):
            continue
        try:
            lease.assert_owned()
            repository.mark_backup_job_pruned(engine, entry.record.id, "missing_file")
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                action="reconciled",
                reason="missing_file",
                item=entry.item,
            )
            reconciled += 1
        except Exception as exc:  # noqa: BLE001 - reconcile each archive independently
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                action="prune_failed",
                reason="metadata_update_failed",
                item=entry.item,
            )
            logger.warning(
                "backup missing-file reconciliation failed backup_id=%s error_type=%s",
                entry.record.id,
                type(exc).__name__,
            )
    return reconciled


def _apply_local_retention(
    engine: Engine,
    config: BackupExecutionConfig,
    lease: BackupOperationLease,
    *,
    operation_id: str,
    as_of: datetime,
    incoming_bytes: int | None = None,
) -> int:
    inventory = collect_local_inventory(engine, config.backup_dir, as_of=as_of)
    plan = _retention_plan(
        as_of=as_of,
        inventory=inventory.items,
        policy=config.local_policy,
        max_bytes=config.local_max_bytes,
        incoming_bytes=incoming_bytes,
    )
    if not config.retention_active:
        return 0
    by_key = {entry.item.archive_key: entry for entry in inventory.entries}
    deleted = 0
    for decision in plan.decisions:
        if decision.action is not RetentionAction.DELETE:
            continue
        entry = by_key.get(decision.archive_key)
        if entry is None or entry.path is None:
            continue
        try:
            lease.assert_owned()
            entry.path.unlink(missing_ok=True)
            repository.mark_backup_job_pruned(engine, entry.record.id, _prune_reason(decision))
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                action="pruned",
                reason=_prune_reason(decision),
                item=entry.item,
            )
            deleted += 1
        except BackupOperationLockLostError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconcile each archive independently
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                action="prune_failed",
                reason="delete_failed",
                item=entry.item,
            )
            logger.warning(
                "local retention failed backup_id=%s error_type=%s",
                entry.record.id,
                type(exc).__name__,
            )
    return deleted


def _remote_inventory_items(
    engine: Engine,
    inventory: smb_backup.SmbInventory,
    *,
    as_of: datetime,
) -> tuple[BackupInventoryItem, ...]:
    jobs = {job.id: job for job in repository.list_backup_jobs(engine)}
    result: list[BackupInventoryItem] = []
    for remote in inventory.items:
        job = jobs.get(remote.job_id)
        if job is not None:
            taken_at = _as_utc(job.started_at)
            source = BackupTimestampSource.JOB_STARTED_AT
        else:
            taken_at = datetime.fromtimestamp(remote.modified_at, tz=UTC)
            source = BackupTimestampSource.REMOTE_MODIFIED_AT
        anomalies = list(remote.anomaly_codes)
        if job is not None and job.size_bytes is not None and job.size_bytes != remote.size_bytes:
            anomalies.append("size_mismatch")
        compatible = True
        if remote.app_version is None:
            anomalies.append("compatibility_unknown")
        elif _version_tuple(remote.app_version) > _version_tuple(APP_VERSION):
            anomalies.append("future_version")
            compatible = False
        if taken_at > as_of:
            anomalies.append("clock_skew")
        result.append(
            BackupInventoryItem(
                destination=BackupDestination.OFFBOX,
                archive_key=remote.filename,
                job_id=remote.job_id,
                taken_at=taken_at,
                timestamp_source=source,
                size_bytes=remote.size_bytes,
                readable=remote.readable_candidate,
                compatible=compatible,
                anomaly_codes=tuple(anomalies),
            )
        )
    return tuple(result)


def _apply_remote_retention(
    engine: Engine,
    config: BackupExecutionConfig,
    lease: BackupOperationLease,
    inventory: smb_backup.SmbInventory,
    *,
    operation_id: str,
    as_of: datetime,
    incoming_bytes: int | None = None,
) -> int:
    assert config.smb_target is not None
    items = _remote_inventory_items(engine, inventory, as_of=as_of)
    plan = _retention_plan(
        as_of=as_of,
        inventory=items,
        policy=config.offbox_policy,
        max_bytes=config.offbox_max_bytes,
        incoming_bytes=incoming_bytes,
    )
    if not config.retention_active:
        return 0
    item_by_key = {item.archive_key: item for item in items}
    deleted = 0
    for decision in plan.decisions:
        if decision.action is not RetentionAction.DELETE:
            continue
        item = item_by_key[decision.archive_key]
        try:
            lease.assert_owned()
            smb_backup.delete(
                config.smb_target,
                item.archive_key,
                assert_mutation_owned=lease.assert_owned,
            )
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="offbox",
                action="pruned",
                reason=_prune_reason(decision),
                item=item,
            )
            deleted += 1
        except BackupOperationLockLostError:
            raise
        except smb_backup.SmbReadProbeError:
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="offbox",
                action="anomaly_detected",
                reason="read_probe_failed",
                item=item,
            )
        except Exception as exc:  # noqa: BLE001 - reconcile each archive independently
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="offbox",
                action="prune_failed",
                reason="delete_failed",
                item=item,
            )
            logger.warning(
                "remote retention failed archive=%s error_type=%s",
                item.archive_key,
                type(exc).__name__,
            )
    return deleted


def _remote_capacity(
    target: smb_backup.SmbTarget,
    *,
    reserve_bytes: int,
    estimate: int | None,
    as_of: datetime,
) -> CapacityObservation:
    try:
        capacity = smb_backup.query_capacity(target)
    except smb_backup.SmbCapacityError as exc:
        if exc.code == "capacity_unsupported":
            return capacity_observation(
                total_bytes=None,
                available_bytes=None,
                reserve_bytes=reserve_bytes,
                estimated_next_backup_bytes=estimate,
                as_of=as_of,
                reason_code=exc.code,
                reason=exc.reason,
            )
        return capacity_observation(
            total_bytes=None,
            available_bytes=None,
            reserve_bytes=reserve_bytes,
            estimated_next_backup_bytes=estimate,
            as_of=as_of,
            unavailable=True,
            reason_code=exc.code,
            reason=exc.reason,
        )
    return capacity_observation(
        total_bytes=capacity.total_bytes,
        available_bytes=capacity.available_bytes,
        reserve_bytes=reserve_bytes,
        estimated_next_backup_bytes=estimate,
        as_of=as_of,
    )


def _safe_job_failure(exc: Exception) -> str:
    if isinstance(exc, BackupOperationLockLostError):
        return "operation_lock_lost"
    if isinstance(exc, BackupConfigurationError):
        return f"BackupConfigurationError: {exc}"
    if isinstance(exc, BackupCommandError):
        return "backup_command_failed"
    if isinstance(exc, BackupEncryptionError):
        return "backup_encryption_failed"
    if isinstance(exc, OSError):
        return "backup_storage_failed"
    return "backup_processing_failed"


def run_backup_once(
    engine: Engine,
    config: BackupExecutionConfig,
    *,
    now: datetime | None = None,
) -> str:
    """Run the complete synchronous lifecycle while owning the mutation lock."""
    as_of = _as_utc(now or datetime.now(UTC))
    lease = acquire_backup_operation_lock(engine)
    operation_id = str(uuid.uuid4())
    job: repository.BackupJobRecord | None = None
    local_completed = False
    with lease:
        try:
            job = repository.create_backup_job(engine, started_at=as_of)
            repository.update_backup_job(engine, job.id, status="running")
            if not config.encryption_key:
                raise BackupConfigurationError("FAMILY_CFO_BACKUP_ENCRYPTION_KEY is not configured")
            os.makedirs(config.backup_dir, exist_ok=True)
            local_inventory = collect_local_inventory(engine, config.backup_dir, as_of=as_of)
            _reconcile_missing_local(
                engine, config, local_inventory, lease, operation_id=operation_id
            )
            local_inventory = collect_local_inventory(engine, config.backup_dir, as_of=as_of)
            estimate = estimate_next_backup_bytes(local_inventory)
            _apply_local_retention(
                engine,
                config,
                lease,
                operation_id=operation_id,
                as_of=as_of,
                incoming_bytes=estimate,
            )
            capacity = query_local_capacity(
                config.backup_dir,
                reserve_bytes=config.local_min_free_bytes,
                estimated_next_backup_bytes=estimate,
                as_of=as_of,
            )
            if capacity.status == "insufficient":
                _journal(
                    engine,
                    config,
                    operation_id=operation_id,
                    destination="local",
                    action="capacity_blocked",
                    reason="local_capacity_insufficient",
                    backup_job_id=job.id,
                )
                raise BackupConfigurationError("local backup capacity is insufficient")

            adapter = select_backup_adapter(
                config.database_url, timeout_seconds=config.io_timeout_seconds
            )
            with tempfile.TemporaryDirectory() as tmp_dir:
                dump_path = Path(tmp_dir) / "database.dump"
                adapter.dump_database(dump_path)
                database_dump = dump_path.read_bytes()
            documents_tar = _tar_directory(config.staging_dir)
            manifest = {
                "app_version": APP_VERSION,
                "schema_revision": _current_schema_revision(engine),
            }
            archive = build_archive(database_dump, documents_tar, manifest=manifest)
            ciphertext = encrypt(config.encryption_key, archive)
            storage_path = atomic_promote_local_archive(
                config.backup_dir, job.id, ciphertext, lease
            )
            repository.complete_backup_job_local(
                engine,
                job.id,
                storage_path=storage_path,
                size_bytes=len(ciphertext),
                remote_status="pending" if config.smb_target is not None else "skipped",
                app_version=APP_VERSION,
                schema_revision=manifest["schema_revision"],
            )
            local_completed = True
            try:
                _apply_local_retention(
                    engine,
                    config,
                    lease,
                    operation_id=operation_id,
                    as_of=as_of,
                )
            except BackupOperationLockLostError:
                raise
            except Exception as exc:  # noqa: BLE001 - postflight cannot undo local success
                _journal(
                    engine,
                    config,
                    operation_id=operation_id,
                    destination="local",
                    action="prune_failed",
                    reason="postflight_failed",
                    backup_job_id=job.id,
                )
                logger.warning(
                    "local postflight failed backup_id=%s error_type=%s",
                    job.id,
                    type(exc).__name__,
                )

            if config.smb_target is not None:
                _sync_remote(
                    engine,
                    config,
                    lease,
                    operation_id=operation_id,
                    as_of=as_of,
                    job_id=job.id,
                    storage_path=storage_path,
                    ciphertext_size=len(ciphertext),
                )
            logger.info(
                "backup completed backup_id=%s size_bytes=%s",
                job.id,
                len(ciphertext),
            )
        except Exception as exc:
            if job is None:
                raise
            reason = _safe_job_failure(exc)
            if local_completed:
                repository.update_backup_job_remote_result(
                    engine, job.id, remote_status="failed", remote_error=reason
                )
            else:
                repository.fail_backup_job_if_running(engine, job.id, reason=reason)
            logger.warning(
                "backup lifecycle failed backup_id=%s error_type=%s",
                job.id,
                type(exc).__name__,
            )
            if isinstance(exc, BackupOperationLockLostError):
                raise
    assert job is not None
    return job.id


def _sync_remote(
    engine: Engine,
    config: BackupExecutionConfig,
    lease: BackupOperationLease,
    *,
    operation_id: str,
    as_of: datetime,
    job_id: str,
    storage_path: str,
    ciphertext_size: int,
) -> None:
    assert config.smb_target is not None
    inventory: smb_backup.SmbInventory | None = None
    try:
        inventory = smb_backup.list_inventory(config.smb_target)
        _journal(
            engine,
            config,
            operation_id=operation_id,
            destination="offbox",
            action="inventory_succeeded",
            reason="inventory_available",
        )
    except smb_backup.SmbInventoryError:
        _journal(
            engine,
            config,
            operation_id=operation_id,
            destination="offbox",
            action="inventory_failed",
            reason="inventory_unavailable",
        )
    try:
        if inventory is not None:
            _apply_remote_retention(
                engine,
                config,
                lease,
                inventory,
                operation_id=operation_id,
                as_of=as_of,
                incoming_bytes=ciphertext_size,
            )
        capacity = _remote_capacity(
            config.smb_target,
            reserve_bytes=config.offbox_min_free_bytes,
            estimate=ciphertext_size,
            as_of=as_of,
        )
        if capacity.status == "insufficient":
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="offbox",
                action="capacity_blocked",
                reason="offbox_capacity_insufficient",
                backup_job_id=job_id,
            )
            repository.update_backup_job_remote_result(
                engine,
                job_id,
                remote_status="failed",
                remote_error="Synology backup capacity is insufficient.",
            )
            return
        full_path = str(
            resolve_managed_local_path(config.backup_dir, storage_path, expected_job_id=job_id)
        )
        smb_backup.upload(
            config.smb_target,
            full_path,
            f"{job_id}.v{APP_VERSION}.enc",
            assert_mutation_owned=lease.assert_owned,
        )
        repository.update_backup_job_remote_result(
            engine, job_id, remote_status="synced", remote_error=None
        )
        try:
            after = smb_backup.list_inventory(config.smb_target)
            _apply_remote_retention(
                engine,
                config,
                lease,
                after,
                operation_id=operation_id,
                as_of=as_of,
            )
        except BackupOperationLockLostError:
            raise
        except Exception as exc:  # noqa: BLE001 - postflight cannot undo remote success
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="offbox",
                action="prune_failed",
                reason="postflight_failed",
                backup_job_id=job_id,
            )
            logger.warning(
                "remote postflight failed backup_id=%s error_type=%s",
                job_id,
                type(exc).__name__,
            )
    except BackupOperationLockLostError:
        raise
    except Exception as exc:  # noqa: BLE001 - remote failure preserves local success
        reason = (
            exc.reason
            if isinstance(exc, smb_backup.SmbStorageError)
            else ("The Synology backup operation failed.")
        )
        repository.update_backup_job_remote_result(
            engine, job_id, remote_status="failed", remote_error=reason
        )
        logger.warning(
            "backup SMB lifecycle failed backup_id=%s error_type=%s",
            job_id,
            type(exc).__name__,
        )


def run_backup_maintenance(
    engine: Engine,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> BackupMaintenanceResult:
    """Run cadence-independent reconciliation and activated retention."""
    as_of = _as_utc(now or datetime.now(UTC))
    config = build_backup_execution_config(engine, settings)
    operation_id = str(uuid.uuid4())
    lease = try_acquire_backup_operation_lock(engine)
    if lease is None:
        _record_lock_skipped(engine, config, operation_id=operation_id, as_of=as_of)
        return BackupMaintenanceResult(lock_skipped=True)
    with lease:
        inventory = collect_local_inventory(engine, config.backup_dir, as_of=as_of)
        reconciled = _reconcile_missing_local(
            engine, config, inventory, lease, operation_id=operation_id
        )
        lease.assert_owned()
        interrupted_ids = repository.mark_interrupted_backup_jobs(
            engine,
            older_than=as_of - timedelta(seconds=config.io_timeout_seconds) - _WORKER_INTERVAL,
        )
        for job_id in interrupted_ids:
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                action="reconciled",
                reason="interrupted",
                backup_job_id=job_id,
            )
        local_partials = 0
        stale_before = as_of - _STALE_PARTIAL_AGE
        for partial in inventory.partial_entries:
            if partial.modified_at >= stale_before:
                continue
            lease.assert_owned()
            try:
                partial.path.unlink(missing_ok=True)
                local_partials += 1
                _journal(
                    engine,
                    config,
                    operation_id=operation_id,
                    destination="local",
                    action="reconciled",
                    reason="stale_partial",
                    archive_key=partial.archive_key,
                )
            except OSError as exc:
                logger.warning("local partial cleanup failed error_type=%s", type(exc).__name__)
        _journal(
            engine,
            config,
            operation_id=operation_id,
            destination="local",
            action="inventory_succeeded",
            reason="inventory_available",
        )
        local_capacity = query_local_capacity(
            config.backup_dir,
            reserve_bytes=config.local_min_free_bytes,
            estimated_next_backup_bytes=estimate_next_backup_bytes(inventory),
            as_of=as_of,
        )
        if local_capacity.status == "insufficient":
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                action="capacity_blocked",
                reason="local_capacity_insufficient",
            )
        local_pruned = _apply_local_retention(
            engine,
            config,
            lease,
            operation_id=operation_id,
            as_of=as_of,
        )
        remote_partials = remote_pruned = 0
        if config.smb_target is not None:
            try:
                remote_inventory = smb_backup.list_inventory(config.smb_target)
                _journal(
                    engine,
                    config,
                    operation_id=operation_id,
                    destination="offbox",
                    action="inventory_succeeded",
                    reason="inventory_available",
                )
                remote_partials = smb_backup.delete_stale_partials(
                    config.smb_target,
                    older_than=int(stale_before.timestamp()),
                    assert_mutation_owned=lease.assert_owned,
                )
                remote_pruned = _apply_remote_retention(
                    engine,
                    config,
                    lease,
                    remote_inventory,
                    operation_id=operation_id,
                    as_of=as_of,
                )
                remote_capacity = _remote_capacity(
                    config.smb_target,
                    reserve_bytes=config.offbox_min_free_bytes,
                    estimate=estimate_next_backup_bytes(inventory),
                    as_of=as_of,
                )
                if remote_capacity.status == "insufficient":
                    _journal(
                        engine,
                        config,
                        operation_id=operation_id,
                        destination="offbox",
                        action="capacity_blocked",
                        reason="offbox_capacity_insufficient",
                    )
            except BackupOperationLockLostError:
                raise
            except Exception as exc:  # noqa: BLE001 - remote maintenance is best-effort
                _journal(
                    engine,
                    config,
                    operation_id=operation_id,
                    destination="offbox",
                    action="inventory_failed",
                    reason="inventory_unavailable",
                )
                logger.warning("remote maintenance unavailable error_type=%s", type(exc).__name__)
        return BackupMaintenanceResult(
            reconciled_jobs=reconciled,
            interrupted_jobs=len(interrupted_ids),
            local_partials_removed=local_partials,
            remote_partials_removed=remote_partials,
            local_pruned=local_pruned,
            remote_pruned=remote_pruned,
        )


def delete_local_backup(engine: Engine, backup_job_id: str, config: BackupExecutionConfig) -> None:
    record = repository.get_backup_job(engine, backup_job_id)
    if record is None:
        raise ValueError("backup job not found")
    operation_id = str(uuid.uuid4())
    with acquire_backup_operation_lock(engine) as lease:
        if record.storage_path:
            path = resolve_managed_local_path(
                config.backup_dir,
                record.storage_path,
                expected_job_id=record.id,
            )
            lease.assert_owned()
            path.unlink(missing_ok=True)
        _journal(
            engine,
            config,
            operation_id=operation_id,
            destination="local",
            action="explicit_deleted",
            reason="explicit_delete",
            archive_key=record.storage_path,
            backup_job_id=record.id,
        )
        lease.assert_owned()
        repository.delete_backup_job(engine, record.id)


def delete_remote_backup(engine: Engine, filename: str, config: BackupExecutionConfig) -> None:
    if config.smb_target is None:
        raise BackupConfigurationError("No Synology backup destination is configured")
    operation_id = str(uuid.uuid4())
    with acquire_backup_operation_lock(engine) as lease:
        lease.assert_owned()
        smb_backup.delete(
            config.smb_target,
            filename,
            assert_mutation_owned=lease.assert_owned,
        )
        _journal(
            engine,
            config,
            operation_id=operation_id,
            destination="offbox",
            action="explicit_deleted",
            reason="explicit_delete",
            archive_key=filename,
        )


def restore_backup(
    engine: Engine,
    backup_job_id: str,
    config: BackupExecutionConfig,
) -> None:
    job = repository.get_backup_job(engine, backup_job_id)
    if job is None:
        raise ValueError(f"backup job {backup_job_id} not found")
    if job.status != "completed" or not job.storage_path:
        raise ValueError(
            f"backup job {backup_job_id} is not a completed backup with a stored archive"
        )
    path = resolve_managed_local_path(config.backup_dir, job.storage_path, expected_job_id=job.id)
    with acquire_backup_operation_lock(engine) as lease:
        ciphertext = path.read_bytes()
        captured_settings = repository.get_backup_settings(engine)
        _restore_ciphertext_locked(
            engine,
            ciphertext,
            config=config,
            lease=lease,
            captured_settings=captured_settings,
            source_job=job,
        )
    logger.info("backup restored backup_id=%s", backup_job_id)


def restore_from_bytes(
    engine: Engine,
    ciphertext: bytes,
    config: BackupExecutionConfig,
) -> None:
    """Restore an already-downloaded remote archive under mutation ownership."""
    with acquire_backup_operation_lock(engine) as lease:
        captured_settings = repository.get_backup_settings(engine)
        _restore_ciphertext_locked(
            engine,
            ciphertext,
            config=config,
            lease=lease,
            captured_settings=captured_settings,
            source_job=None,
        )


def _restore_ciphertext_locked(
    engine: Engine,
    ciphertext: bytes,
    *,
    config: BackupExecutionConfig,
    lease: BackupOperationLease,
    captured_settings: repository.BackupSettingsRecord,
    source_job: repository.BackupJobRecord | None,
) -> None:
    if not config.encryption_key:
        raise BackupConfigurationError("FAMILY_CFO_BACKUP_ENCRYPTION_KEY is not configured")
    archive = decrypt(config.encryption_key, ciphertext)
    database_dump, documents_tar, manifest = extract_archive(archive)
    _check_restore_compatibility(manifest)
    adapter = select_backup_adapter(config.database_url, timeout_seconds=config.io_timeout_seconds)
    with tempfile.TemporaryDirectory() as tmp_dir:
        dump_path = Path(tmp_dir) / "database.dump"
        dump_path.write_bytes(database_dump)
        lease.assert_owned()
        adapter.restore_database(dump_path)
    _migrate_after_restore(
        config.database_url,
        manifest,
        timeout_seconds=config.io_timeout_seconds,
    )
    lease.assert_owned()
    _untar_directory(documents_tar, config.staging_dir)
    lease.assert_owned()
    restored_settings = repository.restore_backup_settings(
        engine,
        captured_settings,
        local_path_fingerprint=repository.backup_local_path_fingerprint(config.backup_dir),
    )
    if source_job is not None:
        lease.assert_owned()
        try:
            path = resolve_managed_local_path(
                config.backup_dir,
                source_job.storage_path or "",
                expected_job_id=source_job.id,
            )
            if path.is_file() and path.stat().st_size > 0:
                with path.open("rb") as handle:
                    if handle.read(1):
                        repository.restore_backup_job_after_restore(engine, source_job)
        except (OSError, UnsafeBackupPathError):
            logger.warning("restore source could not be reconciled")
    lease.assert_owned()
    interrupted = repository.mark_interrupted_backup_jobs(engine)
    operation_id = str(uuid.uuid4())
    restored_config = replace(
        config,
        retention_review_required=True,
        retention_activated_at=None,
        settings_updated_at=restored_settings.updated_at,
        local_destination_generation=restored_settings.local_destination_generation,
        offbox_destination_generation=restored_settings.offbox_destination_generation,
    )
    for destination in ("local", "offbox"):
        _journal(
            engine,
            restored_config,
            operation_id=operation_id,
            destination=destination,
            action="restore_reset",
            reason="restore_policy_review_required",
        )
    if interrupted:
        logger.info("restore reconciled interrupted_backup_jobs=%s", len(interrupted))


def restore_from_path(
    engine: Engine,
    enc_path: str,
    config: BackupExecutionConfig,
) -> None:
    path = Path(enc_path)
    if not path.is_file():
        raise ValueError("backup file not found")
    restore_from_bytes(engine, path.read_bytes(), config)


def _current_schema_revision(engine: Engine) -> str | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        return row[0] if row else None
    except Exception:  # noqa: BLE001 - missing Alembic table is normal in tests
        return None


def _known_migrations() -> tuple[set[str], str | None]:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        revisions = {script.revision for script in scripts.walk_revisions()}
        return revisions, scripts.get_current_head()
    except Exception:  # noqa: BLE001 - compatibility guard degrades when metadata is absent
        return set(), None


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split("."):
        digits = "".join(character for character in piece if character.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _check_restore_compatibility(manifest: dict | None) -> None:
    if manifest is None:
        logger.warning("restore: archive has no version manifest")
        return
    backup_version = manifest.get("app_version")
    if backup_version and _version_tuple(str(backup_version)) > _version_tuple(APP_VERSION):
        raise BackupCompatibilityError(
            f"This backup was made by Family CFO {backup_version}, which is newer than "
            f"this box ({APP_VERSION}). Update the app first, then restore."
        )
    revision = manifest.get("schema_revision")
    if revision:
        known, _head = _known_migrations()
        if known and str(revision) not in known:
            raise BackupCompatibilityError(
                f"This backup uses database revision {revision}, which this version of "
                "the app doesn't know. Update the app first, then restore."
            )


def _migrate_after_restore(
    database_url: str,
    manifest: dict | None,
    *,
    timeout_seconds: int = 3600,
) -> None:
    if manifest is None:
        return
    revision = manifest.get("schema_revision")
    if not revision:
        return
    known, head = _known_migrations()
    if str(revision) not in known or str(revision) == head:
        return
    try:
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
                "head",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.error("restore post-migration timed out")
        return
    if result.returncode != 0:
        logger.error("restore post-migration failed returncode=%s", result.returncode)


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


def verify_destination(path: str) -> tuple[bool, str | None]:
    if not path.strip():
        return False, "No destination path set."
    if not os.path.isdir(path):
        return False, "The destination is not a directory the server can see."
    probe = os.path.join(path, ".family-cfo-write-test")
    try:
        with open(probe, "wb") as handle:
            handle.write(b"ok")
        os.remove(probe)
    except PermissionError:
        return False, "Permission denied writing to the destination."
    except OSError:
        return False, "The destination could not be written."
    return True, None


def _app_version_from_filename(name: str) -> str | None:
    base = name.removesuffix(".enc")
    if ".v" not in base:
        return None
    candidate = base.rsplit(".v", 1)[1]
    return candidate if candidate and candidate[0].isdigit() else None


def list_remote_backups(path: str) -> list[dict]:
    """Legacy mounted-directory reader retained for rollback compatibility."""
    if not path or not os.path.isdir(path):
        return []
    items: list[dict] = []
    for name in os.listdir(path):
        if not name.endswith(".enc"):
            continue
        full = os.path.join(path, name)
        try:
            file_stat = os.stat(full)
        except OSError:
            continue
        items.append(
            {
                "filename": name,
                "size_bytes": file_stat.st_size,
                "modified_at": int(file_stat.st_mtime),
                "app_version": _app_version_from_filename(name),
            }
        )
    items.sort(key=lambda item: (-item["modified_at"], item["filename"]))
    return items


def _enforce_age_cap_remote(
    smb_target: smb_backup.SmbTarget,
    retention_days: int,
    *,
    now: float | None = None,
) -> int:
    """Legacy test/rollback helper; active lifecycle uses tiered planning."""
    current = now if now is not None else time.time()
    cutoff = current - retention_days * 86400
    items = smb_backup.list_backups(smb_target)
    deleted = 0
    for item in items[1:]:
        if item["modified_at"] < cutoff:
            smb_backup.delete(smb_target, item["filename"])
            deleted += 1
    return deleted
