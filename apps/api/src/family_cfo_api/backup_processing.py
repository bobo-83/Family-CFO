from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Callable
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
    BackupOperationBusyError,  # noqa: F401 - API compatibility re-export
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
_API_DIR = Path(__file__).resolve().parents[2]
_ALEMBIC_CONFIG = _API_DIR / "alembic.ini"


class BackupCompatibilityError(ValueError):
    """The archive requires a newer application or database revision."""


class BackupConfigurationError(ValueError):
    """Backup process configuration cannot safely execute."""


class BackupNotFoundError(ValueError):
    """The requested managed backup record does not exist."""


class BackupRestoreError(RuntimeError):
    """A typed restore failure whose message is safe at the HTTP boundary."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(message)


class BackupMigrationError(BackupRestoreError):
    """The staged/restored database could not be migrated to the current schema."""

    def __init__(self, kind: str) -> None:
        message = (
            "Backup database migration timed out; current data was preserved."
            if kind == "timeout"
            else "Backup database migration failed; current data was preserved."
        )
        super().__init__(kind, message)


class BackupDocumentRestoreError(BackupRestoreError):
    """The archive document tree could not be staged or atomically promoted."""

    def __init__(self) -> None:
        super().__init__(
            "documents",
            "Backup documents could not be restored; current data was preserved.",
        )


class BackupDatabaseRestoreError(BackupRestoreError):
    """The archive database could not be promoted and the old database was restored."""

    def __init__(self) -> None:
        super().__init__(
            "database",
            "Backup database could not be restored; current data was preserved.",
        )


class BackupRestoreFinalizationError(BackupRestoreError):
    """Mandatory post-promotion safety state could not be established."""

    def __init__(self) -> None:
        super().__init__(
            "finalization",
            "Backup restore could not be finalized; current data was preserved.",
        )


class BackupRestoreRollbackError(BackupRestoreError):
    """The pre-restore database/document pair could not be verified after rollback."""

    def __init__(self) -> None:
        super().__init__(
            "rollback",
            "Backup restore rollback could not be verified; operator intervention is required.",
        )


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


RestoreBoundary = tuple[int | None, str]


@dataclass(frozen=True, slots=True)
class RestoreResult:
    job: repository.BackupJobRecord
    boundary: RestoreBoundary | None


@dataclass(frozen=True, slots=True)
class RemoteRestoreResult:
    boundary: RestoreBoundary | None
    capacity: CapacityObservation


@dataclass(frozen=True, slots=True)
class RemoteDeleteResult:
    writable: bool
    reason: str | None
    capacity: CapacityObservation


@dataclass(frozen=True, slots=True)
class _DocumentPromotion:
    live_path: Path
    rollback_path: Path | None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def build_backup_execution_config(
    engine: Engine,
    settings: Settings,
    *,
    lease: BackupOperationLease,
) -> BackupExecutionConfig:
    lease.assert_owned()
    if settings.backup_io_timeout_seconds <= 0:
        raise BackupConfigurationError("backup I/O timeout must be positive")
    try:
        stored = repository.ensure_backup_local_destination_generation(engine, settings.backup_dir)
    except repository.BackupSettingsConflictError:
        # A concurrent settings save won the compare-and-swap; rebuild from its
        # authoritative revision rather than mixing two configuration snapshots.
        stored = repository.ensure_backup_local_destination_generation(engine, settings.backup_dir)
    lease.assert_owned()
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
        lease.assert_owned()
        try:
            password = banksync.decrypt_credential(settings, stored.smb_password_encrypted)
        except Exception as exc:  # secret errors must remain redacted
            raise BackupConfigurationError("stored SMB credential could not be opened") from exc
        lease.assert_owned()
        target = smb_backup.SmbTarget(
            host=stored.smb_host,
            share=stored.smb_share,
            folder=stored.smb_folder,
            username=stored.smb_username,
            password=password,
            domain=stored.smb_domain,
            io_timeout_seconds=settings.backup_io_timeout_seconds,
        )
    lease.assert_owned()
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
    operation_id = str(uuid.uuid4())
    lease = try_acquire_backup_operation_lock(engine)
    if lease is None:
        _record_lock_skipped(engine, operation_id=operation_id, as_of=as_of)
        return 0
    with lease:
        config = build_backup_execution_config(engine, settings, lease=lease)
        if config.frequency == "off":
            return 0
        minutes = BACKUP_CADENCE_MINUTES.get(config.frequency, 1440)
        latest = repository.latest_completed_backup_job(engine)
        if latest is not None and latest.completed_at is not None:
            cutoff = as_of - timedelta(minutes=max(1, minutes - 2))
            if _as_utc(latest.completed_at) >= cutoff:
                return 0
        _run_backup_once_locked(
            engine,
            config,
            lease,
            as_of=as_of,
            operation_id=operation_id,
        )
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


def _journal_best_effort(
    engine: Engine,
    config: BackupExecutionConfig,
    **kwargs: object,
) -> None:
    try:
        _journal(engine, config, **kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - observation journaling is non-destructive
        logger.warning(
            "backup journal write failed action=%s reason=%s error_type=%s",
            kwargs.get("action"),
            kwargs.get("reason"),
            type(exc).__name__,
        )


def _journal_delete_pending(
    engine: Engine,
    config: BackupExecutionConfig,
    *,
    operation_id: str,
    destination: str,
    reason: str,
    item: BackupInventoryItem | None = None,
    archive_key: str | None = None,
    backup_job_id: str | None = None,
) -> None:
    """Durably identify a destructive target before touching external storage."""
    _journal(
        engine,
        config,
        operation_id=operation_id,
        destination=destination,
        action="delete_pending",
        reason=reason,
        item=item,
        archive_key=archive_key,
        backup_job_id=backup_job_id,
    )


def _reconcile_delete_intents(
    engine: Engine,
    config: BackupExecutionConfig,
    lease: BackupOperationLease,
    *,
    destination: str,
    visible_archive_keys: set[str],
) -> int:
    generation = (
        config.local_destination_generation
        if destination == "local"
        else config.offbox_destination_generation
    )
    reconciled = 0
    for intent in repository.list_unresolved_backup_delete_intents(
        engine,
        destination=destination,
        destination_generation=generation,
    ):
        if intent.archive_key is not None and intent.archive_key in visible_archive_keys:
            continue
        if destination == "local" and intent.backup_job_id is not None:
            lease.assert_owned()
            if intent.reason == "explicit_delete":
                repository.delete_backup_job(engine, intent.backup_job_id)
            else:
                repository.mark_backup_job_pruned(engine, intent.backup_job_id, intent.reason)
        _journal(
            engine,
            config,
            operation_id=intent.operation_id,
            destination=destination,
            action="reconciled",
            reason=intent.reason,
            archive_key=intent.archive_key,
            backup_job_id=intent.backup_job_id,
        )
        reconciled += 1
    return reconciled


def _record_lock_skipped(
    engine: Engine,
    *,
    operation_id: str,
    as_of: datetime,
) -> None:
    try:
        repository.record_backup_lock_skipped_events(
            engine,
            operation_id=operation_id,
            occurred_at=as_of,
        )
    except Exception as exc:  # noqa: BLE001 - busy journaling is observational
        logger.warning(
            "backup lock-skipped journal failed error_type=%s",
            type(exc).__name__,
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
    inventory.require_available()
    reconciled = 0
    for entry in inventory.entries:
        if not {"missing_file", "missing_storage_path"}.intersection(entry.item.anomaly_codes):
            continue
        try:
            _journal_delete_pending(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                reason="missing_file",
                item=entry.item,
            )
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
        except BackupOperationLockLostError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconcile each archive independently
            _journal_best_effort(
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
    inventory: LocalInventory | None = None,
) -> int:
    inventory = inventory or collect_local_inventory(engine, config.backup_dir, as_of=as_of)
    inventory.require_available()
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
            reason = _prune_reason(decision)
            _journal_delete_pending(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                reason=reason,
                item=entry.item,
            )
            lease.assert_owned()
            entry.path.unlink(missing_ok=True)
            repository.mark_backup_job_pruned(engine, entry.record.id, reason)
            _journal(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                action="pruned",
                reason=reason,
                item=entry.item,
            )
            deleted += 1
        except BackupOperationLockLostError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconcile each archive independently
            _journal_best_effort(
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


def remote_inventory_items(
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
    items = remote_inventory_items(engine, inventory, as_of=as_of)
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
            reason = _prune_reason(decision)
            _journal_delete_pending(
                engine,
                config,
                operation_id=operation_id,
                destination="offbox",
                reason=reason,
                item=item,
            )
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
                reason=reason,
                item=item,
            )
            deleted += 1
        except BackupOperationLockLostError:
            raise
        except smb_backup.SmbReadProbeError:
            _journal_best_effort(
                engine,
                config,
                operation_id=operation_id,
                destination="offbox",
                action="anomaly_detected",
                reason="read_probe_failed",
                item=item,
            )
        except Exception as exc:  # noqa: BLE001 - reconcile each archive independently
            _journal_best_effort(
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


def query_remote_capacity_observation(
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
    settings: Settings,
    *,
    now: datetime | None = None,
    finalize: Callable[[repository.BackupJobRecord], None] | None = None,
) -> repository.BackupJobRecord:
    """Run one lifecycle from configuration snapshot through final result."""
    as_of = _as_utc(now or datetime.now(UTC))
    operation_id = str(uuid.uuid4())
    with acquire_backup_operation_lock(engine) as lease:
        config = build_backup_execution_config(engine, settings, lease=lease)
        return _run_backup_once_locked(
            engine,
            config,
            lease,
            as_of=as_of,
            operation_id=operation_id,
            finalize=finalize,
        )


def _run_backup_once_locked(
    engine: Engine,
    config: BackupExecutionConfig,
    lease: BackupOperationLease,
    *,
    as_of: datetime,
    operation_id: str,
    finalize: Callable[[repository.BackupJobRecord], None] | None = None,
) -> repository.BackupJobRecord:
    """Run a lifecycle using the caller's already-owned immutable snapshot."""
    lease.assert_owned()
    job: repository.BackupJobRecord | None = None
    local_completed = False
    try:
        lease.assert_owned()
        job = repository.create_backup_job(engine, started_at=as_of)
        lease.assert_owned()
        repository.update_backup_job(engine, job.id, status="running")
        if not config.encryption_key:
            raise BackupConfigurationError("FAMILY_CFO_BACKUP_ENCRYPTION_KEY is not configured")
        os.makedirs(config.backup_dir, exist_ok=True)
        local_inventory = collect_local_inventory(engine, config.backup_dir, as_of=as_of)
        local_inventory.require_available()
        _reconcile_missing_local(engine, config, local_inventory, lease, operation_id=operation_id)
        local_inventory = collect_local_inventory(engine, config.backup_dir, as_of=as_of)
        local_inventory.require_available()
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
            _journal_best_effort(
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
        storage_path = atomic_promote_local_archive(config.backup_dir, job.id, ciphertext, lease)
        lease.assert_owned()
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
            _journal_best_effort(
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
    except BackupOperationLockLostError:
        raise
    except Exception as exc:
        if job is None:
            raise
        reason = _safe_job_failure(exc)
        lease.assert_owned()
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

    assert job is not None
    lease.assert_owned()
    record = repository.get_backup_job(engine, job.id)
    if record is None:
        raise RuntimeError("backup job disappeared before finalization")
    if finalize is not None:
        lease.assert_owned()
        finalize(record)
    lease.assert_owned()
    final_record = repository.get_backup_job(engine, job.id)
    if final_record is None:
        raise RuntimeError("backup job disappeared before response finalization")
    lease.assert_owned()
    return final_record


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
        _journal_best_effort(
            engine,
            config,
            operation_id=operation_id,
            destination="offbox",
            action="inventory_succeeded",
            reason="inventory_available",
        )
    except smb_backup.SmbInventoryError:
        _journal_best_effort(
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
        capacity = query_remote_capacity_observation(
            config.smb_target,
            reserve_bytes=config.offbox_min_free_bytes,
            estimate=ciphertext_size,
            as_of=as_of,
        )
        if capacity.status == "insufficient":
            _journal_best_effort(
                engine,
                config,
                operation_id=operation_id,
                destination="offbox",
                action="capacity_blocked",
                reason="offbox_capacity_insufficient",
                backup_job_id=job_id,
            )
            lease.assert_owned()
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
        lease.assert_owned()
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
            _journal_best_effort(
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
        lease.assert_owned()
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
    operation_id = str(uuid.uuid4())
    lease = try_acquire_backup_operation_lock(engine)
    if lease is None:
        _record_lock_skipped(engine, operation_id=operation_id, as_of=as_of)
        return BackupMaintenanceResult(lock_skipped=True)
    with lease:
        config = build_backup_execution_config(engine, settings, lease=lease)
        inventory = collect_local_inventory(engine, config.backup_dir, as_of=as_of)
        if inventory.available:
            visible_local_keys = {
                entry.item.archive_key for entry in inventory.entries if entry.item.present
            }
            visible_local_keys.update(item.archive_key for item in inventory.protected_entries)
            visible_local_keys.update(item.archive_key for item in inventory.partial_entries)
            reconciled = _reconcile_missing_local(
                engine, config, inventory, lease, operation_id=operation_id
            )
            _reconcile_delete_intents(
                engine,
                config,
                lease,
                destination="local",
                visible_archive_keys=visible_local_keys,
            )
        else:
            reconciled = 0
            _journal_best_effort(
                engine,
                config,
                operation_id=operation_id,
                destination="local",
                action="inventory_failed",
                reason=inventory.failure_code or "local_inventory_unavailable",
            )
        lease.assert_owned()
        interrupted_ids = repository.mark_interrupted_backup_jobs(
            engine,
            older_than=as_of - timedelta(seconds=config.io_timeout_seconds) - _WORKER_INTERVAL,
        )
        for job_id in interrupted_ids:
            _journal_best_effort(
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
        for partial in inventory.partial_entries if inventory.available else ():
            if partial.modified_at >= stale_before:
                continue
            try:
                _journal_delete_pending(
                    engine,
                    config,
                    operation_id=operation_id,
                    destination="local",
                    reason="stale_partial",
                    archive_key=partial.archive_key,
                )
                lease.assert_owned()
                partial.path.unlink(missing_ok=True)
                _journal(
                    engine,
                    config,
                    operation_id=operation_id,
                    destination="local",
                    action="reconciled",
                    reason="stale_partial",
                    archive_key=partial.archive_key,
                )
                local_partials += 1
            except BackupOperationLockLostError:
                raise
            except Exception as exc:  # noqa: BLE001 - pending intent supports reconciliation
                _journal_best_effort(
                    engine,
                    config,
                    operation_id=operation_id,
                    destination="local",
                    action="prune_failed",
                    reason="stale_partial_delete_failed",
                    archive_key=partial.archive_key,
                )
                logger.warning("local partial cleanup failed error_type=%s", type(exc).__name__)
        if inventory.available:
            _journal_best_effort(
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
                _journal_best_effort(
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
                inventory=inventory,
            )
        else:
            local_pruned = 0
        remote_partials = remote_pruned = 0
        if config.smb_target is not None:
            try:
                remote_inventory = smb_backup.list_inventory(config.smb_target)
                visible_remote_keys = {item.filename for item in remote_inventory.items}
                visible_remote_keys.update(
                    item.filename for item in remote_inventory.protected_entries
                )
                _reconcile_delete_intents(
                    engine,
                    config,
                    lease,
                    destination="offbox",
                    visible_archive_keys=visible_remote_keys,
                )
                _journal_best_effort(
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
                    before_delete=lambda archive_key: _journal_delete_pending(
                        engine,
                        config,
                        operation_id=operation_id,
                        destination="offbox",
                        reason="stale_partial",
                        archive_key=archive_key,
                    ),
                    after_delete=lambda archive_key: _journal(
                        engine,
                        config,
                        operation_id=operation_id,
                        destination="offbox",
                        action="reconciled",
                        reason="stale_partial",
                        archive_key=archive_key,
                    ),
                )
                remote_pruned = _apply_remote_retention(
                    engine,
                    config,
                    lease,
                    remote_inventory,
                    operation_id=operation_id,
                    as_of=as_of,
                )
                remote_capacity = query_remote_capacity_observation(
                    config.smb_target,
                    reserve_bytes=config.offbox_min_free_bytes,
                    estimate=estimate_next_backup_bytes(inventory),
                    as_of=as_of,
                )
                if remote_capacity.status == "insufficient":
                    _journal_best_effort(
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
                _journal_best_effort(
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


def _remote_capacity_for_config(
    engine: Engine,
    config: BackupExecutionConfig,
    *,
    as_of: datetime,
) -> CapacityObservation:
    assert config.smb_target is not None
    local = collect_local_inventory(engine, config.backup_dir, as_of=as_of)
    estimate = estimate_next_backup_bytes(local) if local.available else None
    return query_remote_capacity_observation(
        config.smb_target,
        reserve_bytes=config.offbox_min_free_bytes,
        estimate=estimate,
        as_of=as_of,
    )


def delete_local_backup(
    engine: Engine,
    backup_job_id: str,
    settings: Settings,
    *,
    finalize: Callable[[], None] | None = None,
) -> None:
    operation_id = str(uuid.uuid4())
    with acquire_backup_operation_lock(engine) as lease:
        config = build_backup_execution_config(engine, settings, lease=lease)
        record = repository.get_backup_job(engine, backup_job_id)
        if record is None:
            raise BackupNotFoundError("backup job not found")
        path = None
        if record.storage_path:
            path = resolve_managed_local_path(
                config.backup_dir,
                record.storage_path,
                expected_job_id=record.id,
            )
        _journal_delete_pending(
            engine,
            config,
            operation_id=operation_id,
            destination="local",
            reason="explicit_delete",
            archive_key=record.storage_path,
            backup_job_id=record.id,
        )
        if path is not None:
            lease.assert_owned()
            path.unlink(missing_ok=True)
        lease.assert_owned()
        repository.delete_backup_job(engine, record.id)
        lease.assert_owned()
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
        if finalize is not None:
            lease.assert_owned()
            finalize()
        lease.assert_owned()


def delete_remote_backup(
    engine: Engine,
    filename: str,
    settings: Settings,
    *,
    finalize: Callable[[], None] | None = None,
    now: datetime | None = None,
) -> RemoteDeleteResult:
    operation_id = str(uuid.uuid4())
    as_of = _as_utc(now or datetime.now(UTC))
    with acquire_backup_operation_lock(engine) as lease:
        config = build_backup_execution_config(engine, settings, lease=lease)
        if config.smb_target is None:
            raise BackupConfigurationError("No Synology backup destination is configured")
        _journal_delete_pending(
            engine,
            config,
            operation_id=operation_id,
            destination="offbox",
            reason="explicit_delete",
            archive_key=filename,
        )
        lease.assert_owned()
        try:
            smb_backup.delete(
                config.smb_target,
                filename,
                assert_mutation_owned=lease.assert_owned,
            )
        except (BackupOperationLockLostError, ValueError):
            raise
        except Exception as exc:  # noqa: BLE001 - preserve the API's typed unavailable result
            reason = smb_backup._friendly(exc)
            return RemoteDeleteResult(
                writable=False,
                reason=reason,
                capacity=capacity_observation(
                    total_bytes=None,
                    available_bytes=None,
                    reserve_bytes=config.offbox_min_free_bytes,
                    estimated_next_backup_bytes=None,
                    as_of=as_of,
                    unavailable=True,
                    reason_code="destination_unwritable",
                    reason=reason,
                ),
            )
        lease.assert_owned()
        _journal(
            engine,
            config,
            operation_id=operation_id,
            destination="offbox",
            action="explicit_deleted",
            reason="explicit_delete",
            archive_key=filename,
        )
        if finalize is not None:
            lease.assert_owned()
            finalize()
        lease.assert_owned()
        capacity = _remote_capacity_for_config(engine, config, as_of=as_of)
        lease.assert_owned()
        return RemoteDeleteResult(writable=True, reason=None, capacity=capacity)


def restore_backup(
    engine: Engine,
    backup_job_id: str,
    settings: Settings,
    *,
    before_restore: Callable[[datetime | None], RestoreBoundary] | None = None,
    finalize: Callable[[RestoreBoundary], None] | None = None,
) -> RestoreResult:
    with acquire_backup_operation_lock(engine) as lease:
        config = build_backup_execution_config(engine, settings, lease=lease)
        job = repository.get_backup_job(engine, backup_job_id)
        if job is None:
            raise BackupNotFoundError(f"backup job {backup_job_id} not found")
        if job.status != "completed" or not job.storage_path:
            raise ValueError(
                f"backup job {backup_job_id} is not a completed backup with a stored archive"
            )
        path = resolve_managed_local_path(
            config.backup_dir,
            job.storage_path,
            expected_job_id=job.id,
        )
        lease.assert_owned()
        ciphertext = path.read_bytes()
        boundary = (
            before_restore(job.started_at or job.created_at) if before_restore is not None else None
        )
        lease.assert_owned()
        captured_settings = repository.get_backup_settings(engine)

        def complete_restore() -> repository.BackupJobRecord:
            if finalize is not None and boundary is not None:
                lease.assert_owned()
                finalize(boundary)
            lease.assert_owned()
            restored_job = repository.get_backup_job(engine, backup_job_id)
            if restored_job is None:
                raise RuntimeError("restored backup job is unavailable for the response")
            # Reading a response-driving record is not proof that we still own
            # the mutation lease. Certify ownership after the read as well.
            lease.assert_owned()
            return restored_job

        restored_job = _restore_ciphertext_locked(
            engine,
            ciphertext,
            config=config,
            lease=lease,
            captured_settings=captured_settings,
            source_job=job,
            complete=complete_restore,
        )
        assert restored_job is not None
        logger.info("backup restored backup_id=%s", backup_job_id)
        return RestoreResult(job=restored_job, boundary=boundary)


def restore_from_bytes(
    engine: Engine,
    ciphertext: bytes,
    settings: Settings,
) -> None:
    """Restore caller-provided bytes under mutation ownership."""
    with acquire_backup_operation_lock(engine) as lease:
        config = build_backup_execution_config(engine, settings, lease=lease)
        captured_settings = repository.get_backup_settings(engine)
        _restore_ciphertext_locked(
            engine,
            ciphertext,
            config=config,
            lease=lease,
            captured_settings=captured_settings,
            source_job=None,
        )


def restore_remote_backup(
    engine: Engine,
    filename: str,
    settings: Settings,
    *,
    before_restore: Callable[[datetime | None], RestoreBoundary] | None = None,
    finalize: Callable[[RestoreBoundary], None] | None = None,
    now: datetime | None = None,
) -> RemoteRestoreResult:
    """Hold one lease from target selection through audit and response capacity."""
    as_of = _as_utc(now or datetime.now(UTC))
    with acquire_backup_operation_lock(engine) as lease:
        config = build_backup_execution_config(engine, settings, lease=lease)
        if config.smb_target is None:
            raise BackupConfigurationError("No Synology backup destination is configured")
        snapshot_at: datetime | None = None
        for item in smb_backup.list_backups(config.smb_target):
            if item["filename"] == filename:
                snapshot_at = datetime.fromtimestamp(item["modified_at"], tz=UTC)
                break
        lease.assert_owned()
        ciphertext = smb_backup.download(config.smb_target, filename)
        boundary = before_restore(snapshot_at) if before_restore is not None else None
        lease.assert_owned()
        captured_settings = repository.get_backup_settings(engine)

        def complete_restore() -> CapacityObservation:
            if finalize is not None and boundary is not None:
                lease.assert_owned()
                finalize(boundary)
            lease.assert_owned()
            capacity = _remote_capacity_for_config(engine, config, as_of=as_of)
            lease.assert_owned()
            return capacity

        capacity = _restore_ciphertext_locked(
            engine,
            ciphertext,
            config=config,
            lease=lease,
            captured_settings=captured_settings,
            source_job=None,
            complete=complete_restore,
        )
        assert capacity is not None
        return RemoteRestoreResult(boundary=boundary, capacity=capacity)


def _restore_ciphertext_locked[RestoreCompletionT](
    engine: Engine,
    ciphertext: bytes,
    *,
    config: BackupExecutionConfig,
    lease: BackupOperationLease,
    captured_settings: repository.BackupSettingsRecord,
    source_job: repository.BackupJobRecord | None,
    complete: Callable[[], RestoreCompletionT] | None = None,
) -> RestoreCompletionT | None:
    if not config.encryption_key:
        raise BackupConfigurationError("FAMILY_CFO_BACKUP_ENCRYPTION_KEY is not configured")
    archive = decrypt(config.encryption_key, ciphertext)
    database_dump, documents_tar, manifest = extract_archive(archive)
    _check_restore_compatibility(manifest)
    adapter = select_backup_adapter(config.database_url, timeout_seconds=config.io_timeout_seconds)
    staged_documents = _stage_restore_documents(documents_tar, config.staging_dir)
    document_promotion: _DocumentPromotion | None = None
    database_touched = False
    completion_result: RestoreCompletionT | None = None
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            dump_path = Path(tmp_dir) / "database.dump"
            rollback_path = Path(tmp_dir) / "database.rollback"
            try:
                dump_path.write_bytes(database_dump)
            except OSError as exc:
                raise BackupDatabaseRestoreError() from exc

            # SQLite's archive is itself a database file, so migrate that isolated
            # file before it can touch the live database. PostgreSQL custom dumps
            # cannot be migrated in place; they use the verified rollback image
            # below if live migration fails.
            sqlite_restore = _database_scheme(config.database_url) == "sqlite"
            if sqlite_restore:
                try:
                    lease.assert_owned()
                    _migrate_after_restore(
                        _sqlite_database_url(dump_path),
                        manifest,
                        timeout_seconds=config.io_timeout_seconds,
                    )
                except BackupMigrationError:
                    # The live DB was never replaced, but a failed destructive
                    # restore still pauses pruning and rotates both destination
                    # scopes before the redacted failure reaches the caller.
                    try:
                        _reset_restore_policy(
                            engine,
                            config=config,
                            lease=lease,
                            captured_settings=captured_settings,
                        )
                    except Exception as safety_exc:
                        raise BackupRestoreRollbackError() from safety_exc
                    raise

            # Establish an archive-external nonce in the current settings row
            # before taking the rollback image. A restore archive necessarily
            # predates this value, so a no-op or wrong-image rollback cannot pass
            # verification merely because both databases are at the same schema.
            try:
                rollback_marker_settings = _reset_restore_policy(
                    engine,
                    config=config,
                    lease=lease,
                    captured_settings=captured_settings,
                )
                lease.assert_owned()
            except BackupOperationLockLostError:
                raise
            except Exception as exc:
                raise BackupRestoreFinalizationError() from exc

            pre_restore_revision = _current_schema_revision(engine)
            try:
                adapter.dump_database(rollback_path)
            except Exception as exc:
                raise BackupDatabaseRestoreError() from exc

            try:
                lease.assert_owned()
                # Assume the live database may have been partially changed as soon
                # as restore starts; even a command error must use the rollback image.
                database_touched = True
                adapter.restore_database(dump_path)
                lease.assert_owned()
                if not sqlite_restore:
                    _migrate_after_restore(
                        config.database_url,
                        manifest,
                        timeout_seconds=config.io_timeout_seconds,
                    )
                    lease.assert_owned()

                _finalize_restored_database(
                    engine,
                    config=config,
                    lease=lease,
                    captured_settings=captured_settings,
                    source_job=source_job,
                )
                lease.assert_owned()

                # The archive tree was fully extracted before database mutation.
                # Promote it only after the restored database is current and all
                # mandatory retention/source reconciliation is durable.
                document_promotion = _promote_staged_documents(
                    staged_documents,
                    config.staging_dir,
                )
                lease.assert_owned()

                # Keep both the database rollback image and the previous document
                # tree until audit finalization and all response-driving reads are
                # complete and certified under the same lease.
                if complete is not None:
                    completion_result = complete()
                    lease.assert_owned()
            except Exception as primary:
                rollback_failure: Exception | None = None
                compensation_lease = lease
                reacquired_lease: BackupOperationLease | None = None
                ownership_lost = isinstance(primary, BackupOperationLockLostError)
                if not ownership_lost:
                    try:
                        lease.assert_owned()
                    except BackupOperationLockLostError:
                        ownership_lost = True
                if ownership_lost:
                    # Never compensate after losing exclusivity unless this
                    # operation can establish a fresh lease. A new owner may have
                    # already begun valid work that rollback must not overwrite.
                    try:
                        reacquired_lease = try_acquire_backup_operation_lock(engine)
                    except Exception as reacquire_exc:
                        logger.critical(
                            "restore compensation lease reacquisition failed error_type=%s",
                            type(reacquire_exc).__name__,
                        )
                        raise BackupRestoreRollbackError() from reacquire_exc
                    if reacquired_lease is None:
                        logger.critical(
                            "restore compensation could not reacquire exclusive ownership"
                        )
                        raise BackupRestoreRollbackError() from primary
                    compensation_lease = reacquired_lease
                    try:
                        compensation_lease.assert_owned()
                    except Exception as reacquire_exc:
                        reacquired_lease.release()
                        raise BackupRestoreRollbackError() from reacquire_exc
                try:
                    if document_promotion is not None:
                        try:
                            _rollback_document_promotion(document_promotion)
                            compensation_lease.assert_owned()
                        except Exception as exc:  # noqa: BLE001 - failed safety recovery
                            rollback_failure = exc
                    if database_touched:
                        try:
                            _rollback_database(
                                adapter,
                                rollback_path,
                                engine=engine,
                                lease=compensation_lease,
                                expected_revision=pre_restore_revision,
                                expected_settings=rollback_marker_settings,
                            )
                        except Exception as exc:  # noqa: BLE001 - failed safety recovery
                            rollback_failure = rollback_failure or exc
                    if rollback_failure is None and database_touched:
                        try:
                            _reset_restore_policy(
                                engine,
                                config=config,
                                lease=compensation_lease,
                                captured_settings=captured_settings,
                            )
                            compensation_lease.assert_owned()
                        except Exception as exc:  # noqa: BLE001 - mandatory fail-closed state
                            rollback_failure = exc
                finally:
                    if reacquired_lease is not None:
                        reacquired_lease.release()
                if rollback_failure is not None:
                    logger.critical(
                        "restore rollback could not be verified error_type=%s",
                        type(rollback_failure).__name__,
                    )
                    raise BackupRestoreRollbackError() from rollback_failure
                if isinstance(primary, (BackupRestoreError, BackupOperationLockLostError)):
                    raise
                if isinstance(primary, BackupCommandError):
                    raise BackupDatabaseRestoreError() from primary
                raise BackupRestoreFinalizationError() from primary

            assert document_promotion is not None
            _discard_document_rollback(document_promotion)
        return completion_result
    except BackupRestoreError:
        raise
    except OSError as exc:
        raise BackupDatabaseRestoreError() from exc
    finally:
        try:
            _remove_restore_path(staged_documents)
        except OSError as exc:
            logger.warning(
                "restore document staging cleanup failed error_type=%s",
                type(exc).__name__,
            )


def _database_scheme(database_url: str) -> str:
    return database_url.split("://", 1)[0].split("+")[0]


def _sqlite_database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.resolve()}"


def _stage_restore_documents(data: bytes, directory: str) -> Path:
    live_path = Path(directory).expanduser().absolute()
    try:
        live_path.parent.mkdir(parents=True, exist_ok=True)
        staged = Path(
            tempfile.mkdtemp(
                prefix=f".{live_path.name}.restore-new-",
                dir=live_path.parent,
            )
        )
        try:
            _untar_directory(data, str(staged))
            mode = live_path.stat().st_mode & 0o777 if live_path.is_dir() else 0o755
            staged.chmod(mode)
        except Exception:
            _remove_restore_path(staged)
            raise
        return staged
    except BackupRestoreError:
        raise
    except Exception as exc:
        raise BackupDocumentRestoreError() from exc


def _promote_staged_documents(staged: Path, directory: str) -> _DocumentPromotion:
    live_path = Path(directory).expanduser().absolute()
    rollback_path: Path | None = None
    try:
        if os.path.lexists(live_path):
            rollback_path = live_path.parent / f".{live_path.name}.restore-old-{uuid.uuid4()}"
            os.replace(live_path, rollback_path)
        try:
            os.replace(staged, live_path)
        except Exception:
            if rollback_path is not None:
                os.replace(rollback_path, live_path)
            raise
        return _DocumentPromotion(live_path=live_path, rollback_path=rollback_path)
    except BackupRestoreRollbackError:
        raise
    except Exception as exc:
        if rollback_path is not None and not os.path.lexists(live_path):
            try:
                os.replace(rollback_path, live_path)
            except Exception as rollback_exc:
                raise BackupRestoreRollbackError() from rollback_exc
        raise BackupDocumentRestoreError() from exc


def _rollback_document_promotion(promotion: _DocumentPromotion) -> None:
    _remove_restore_path(promotion.live_path)
    if promotion.rollback_path is not None:
        os.replace(promotion.rollback_path, promotion.live_path)


def _discard_document_rollback(promotion: _DocumentPromotion) -> None:
    if promotion.rollback_path is None:
        return
    try:
        _remove_restore_path(promotion.rollback_path)
    except OSError as exc:
        # The live old/new pair is already certified. A hidden stale rollback
        # directory is operational debris, not a reason to undo a good restore.
        logger.warning(
            "restore document rollback cleanup failed error_type=%s",
            type(exc).__name__,
        )


def _remove_restore_path(path: Path) -> None:
    if not os.path.lexists(path):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _rollback_database(
    adapter: BackupAdapter,
    rollback_path: Path,
    *,
    engine: Engine,
    lease: BackupOperationLease,
    expected_revision: str | None,
    expected_settings: repository.BackupSettingsRecord,
) -> None:
    lease.assert_owned()
    adapter.restore_database(rollback_path)
    lease.assert_owned()
    actual_revision = _current_schema_revision(engine)
    actual_settings = repository.get_backup_settings(engine)
    lease.assert_owned()
    if actual_revision != expected_revision or actual_settings != expected_settings:
        raise RuntimeError("database rollback verification failed")


def _finalize_restored_database(
    engine: Engine,
    *,
    config: BackupExecutionConfig,
    lease: BackupOperationLease,
    captured_settings: repository.BackupSettingsRecord,
    source_job: repository.BackupJobRecord | None,
) -> None:
    _reset_restore_policy(
        engine,
        config=config,
        lease=lease,
        captured_settings=captured_settings,
    )

    if source_job is not None:
        lease.assert_owned()
        path = resolve_managed_local_path(
            config.backup_dir,
            source_job.storage_path or "",
            expected_job_id=source_job.id,
        )
        if not path.is_file() or path.stat().st_size <= 0:
            raise OSError("restore source archive is no longer readable")
        with path.open("rb") as handle:
            if handle.read(1) == b"":
                raise OSError("restore source archive is no longer readable")
        repository.restore_backup_job_after_restore(engine, source_job)

    lease.assert_owned()
    interrupted = repository.mark_interrupted_backup_jobs(engine)
    if interrupted:
        logger.info("restore reconciled interrupted_backup_jobs=%s", len(interrupted))


def _reset_restore_policy(
    engine: Engine,
    *,
    config: BackupExecutionConfig,
    lease: BackupOperationLease,
    captured_settings: repository.BackupSettingsRecord,
) -> repository.BackupSettingsRecord:
    lease.assert_owned()
    restored_settings = repository.restore_backup_settings(
        engine,
        captured_settings,
        local_path_fingerprint=repository.backup_local_path_fingerprint(config.backup_dir),
    )
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
        lease.assert_owned()
        _journal(
            engine,
            restored_config,
            operation_id=operation_id,
            destination=destination,
            action="restore_reset",
            reason="restore_policy_review_required",
        )
    return restored_settings


def restore_from_path(
    engine: Engine,
    enc_path: str,
    settings: Settings,
) -> None:
    path = Path(enc_path)
    if not path.is_file():
        raise ValueError("backup file not found")
    restore_from_bytes(engine, path.read_bytes(), settings)


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

        scripts = ScriptDirectory.from_config(Config(str(_ALEMBIC_CONFIG)))
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
                str(_ALEMBIC_CONFIG),
                "-x",
                f"database_url={database_url}",
                "upgrade",
                "head",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            cwd=_API_DIR,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("restore post-migration timed out")
        raise BackupMigrationError("timeout") from exc
    if result.returncode != 0:
        logger.error("restore post-migration failed returncode=%s", result.returncode)
        raise BackupMigrationError("nonzero_exit")


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
