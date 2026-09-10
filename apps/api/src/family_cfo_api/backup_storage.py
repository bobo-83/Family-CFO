"""Strict local backup inventory, capacity, and atomic file promotion."""

from __future__ import annotations

import math
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from family_cfo_api import __version__ as APP_VERSION
from family_cfo_api import repository
from family_cfo_api.backup_operation_lock import BackupOperationLease
from family_cfo_api.backup_retention import (
    BackupDestination,
    BackupInventoryItem,
    BackupTimestampSource,
)

_MIB = 1024 * 1024


class UnsafeBackupPathError(ValueError):
    """A database path does not identify its managed archive below backup_dir."""


class LocalArchiveConflictError(FileExistsError):
    """A final archive already exists and must never be overwritten."""


@dataclass(frozen=True, slots=True)
class LocalInventoryEntry:
    record: repository.BackupJobRecord
    path: Path | None
    item: BackupInventoryItem


@dataclass(frozen=True, slots=True)
class LocalProtectedEntry:
    archive_key: str
    size_bytes: int | None
    modified_at: datetime | None
    anomaly_code: str


@dataclass(frozen=True, slots=True)
class LocalPartialEntry:
    archive_key: str
    path: Path
    size_bytes: int
    modified_at: datetime


@dataclass(frozen=True, slots=True)
class LocalInventory:
    entries: tuple[LocalInventoryEntry, ...]
    protected_entries: tuple[LocalProtectedEntry, ...]
    partial_entries: tuple[LocalPartialEntry, ...]

    @property
    def items(self) -> tuple[BackupInventoryItem, ...]:
        return tuple(entry.item for entry in self.entries)


@dataclass(frozen=True, slots=True)
class CapacityObservation:
    status: Literal["ok", "warning", "insufficient", "unknown", "unavailable"]
    total_bytes: int | None
    available_bytes: int | None
    reserve_bytes: int
    estimated_next_backup_bytes: int | None
    can_accept_estimated_backup: bool | None
    as_of: datetime
    reason_code: str
    reason: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _version_tuple(version: str) -> tuple[int, ...]:
    values: list[int] = []
    for piece in version.split("."):
        digits = "".join(character for character in piece if character.isdigit())
        values.append(int(digits) if digits else 0)
    return tuple(values)


def _managed_local_name(job_id: str) -> str:
    uuid.UUID(job_id)
    return f"{job_id}.enc"


def resolve_managed_local_path(
    backup_dir: str,
    storage_path: str,
    *,
    expected_job_id: str | None = None,
) -> Path:
    """Resolve a managed relative name without following it outside backup_dir."""
    if not storage_path or os.path.isabs(storage_path):
        raise UnsafeBackupPathError("backup storage path must be relative")
    if Path(storage_path).name != storage_path:
        raise UnsafeBackupPathError("backup storage path must be a basename")
    if expected_job_id is not None and storage_path != _managed_local_name(expected_job_id):
        raise UnsafeBackupPathError("backup storage path does not match its job")
    base = Path(backup_dir).expanduser().resolve()
    candidate = base / storage_path
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(base)
    except (OSError, ValueError) as exc:
        raise UnsafeBackupPathError("backup storage path escapes its destination") from exc
    if candidate.is_symlink():
        raise UnsafeBackupPathError("backup storage path must not be a symlink")
    return candidate


def _qualify_job(
    record: repository.BackupJobRecord,
    backup_dir: str,
    as_of: datetime,
) -> LocalInventoryEntry:
    anomalies: list[str] = []
    path: Path | None = None
    present = readable = compatible = True
    actual_size = record.size_bytes or 0
    if record.storage_path is None:
        anomalies.append("missing_storage_path")
        present = readable = False
    else:
        try:
            path = resolve_managed_local_path(
                backup_dir, record.storage_path, expected_job_id=record.id
            )
        except UnsafeBackupPathError:
            anomalies.append("unsafe_storage_path")
            present = readable = False
        if path is not None:
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    anomalies.append("non_regular_file")
                    readable = False
                else:
                    actual_size = int(info.st_size)
                    if actual_size == 0:
                        anomalies.append("zero_byte")
                        readable = False
                    else:
                        with path.open("rb") as handle:
                            if handle.read(1) == b"":
                                anomalies.append("unreadable")
                                readable = False
                    if record.size_bytes is not None and actual_size != record.size_bytes:
                        anomalies.append("size_mismatch")
                        readable = False
            except FileNotFoundError:
                anomalies.append("missing_file")
                present = readable = False
            except OSError:
                anomalies.append("unreadable")
                readable = False
    if record.app_version is None:
        anomalies.append("compatibility_unknown")
    elif _version_tuple(record.app_version) > _version_tuple(APP_VERSION):
        anomalies.append("future_version")
        compatible = False
    taken_at = _utc(record.started_at)
    if taken_at > as_of:
        anomalies.append("clock_skew")
    item = BackupInventoryItem(
        destination=BackupDestination.LOCAL,
        archive_key=record.storage_path or f"{record.id}.enc",
        job_id=record.id,
        taken_at=taken_at,
        timestamp_source=BackupTimestampSource.JOB_STARTED_AT,
        size_bytes=max(0, actual_size),
        present=present,
        readable=readable,
        compatible=compatible,
        anomaly_codes=tuple(anomalies),
    )
    return LocalInventoryEntry(record, path, item)


def collect_local_inventory(
    engine,
    backup_dir: str,
    *,
    as_of: datetime | None = None,
) -> LocalInventory:
    """Qualify managed rows and protect every archive-shaped unowned entry."""
    now = _utc(as_of or datetime.now(UTC))
    base = Path(backup_dir).expanduser().resolve()
    entries = tuple(
        _qualify_job(record, str(base), now)
        for record in repository.list_completed_backup_jobs_for_inventory(engine)
    )
    owned_names = {
        entry.record.storage_path for entry in entries if entry.record.storage_path is not None
    }
    protected: list[LocalProtectedEntry] = []
    partials: list[LocalPartialEntry] = []
    try:
        children = list(base.iterdir())
    except OSError:
        children = []
    for path in children:
        name = path.name
        if not name.endswith((".enc", ".partial")):
            continue
        try:
            info = path.lstat()
            size = int(info.st_size)
            modified = datetime.fromtimestamp(info.st_mtime, tz=UTC)
        except OSError:
            protected.append(LocalProtectedEntry(name, None, None, "unreadable"))
            continue
        if name.endswith(".partial"):
            partials.append(LocalPartialEntry(name, path, size, modified))
            continue
        if name in owned_names:
            continue
        anomaly = "orphan_archive" if stat.S_ISREG(info.st_mode) else "non_regular_file"
        protected.append(LocalProtectedEntry(name, size, modified, anomaly))
    protected.sort(key=lambda item: item.archive_key)
    partials.sort(key=lambda item: item.archive_key)
    return LocalInventory(entries, tuple(protected), tuple(partials))


def estimate_next_backup_bytes(inventory: LocalInventory) -> int | None:
    eligible = [
        item.size_bytes
        for item in inventory.items
        if item.present
        and item.readable
        and item.compatible
        and item.size_bytes > 0
        and not (set(item.anomaly_codes) - {"compatibility_unknown"})
    ][:3]
    if not eligible:
        return None
    estimated = math.ceil(max(eligible) * 1.1)
    return math.ceil(estimated / _MIB) * _MIB


def capacity_observation(
    *,
    total_bytes: int | None,
    available_bytes: int | None,
    reserve_bytes: int,
    estimated_next_backup_bytes: int | None,
    as_of: datetime,
    unavailable: bool = False,
    reason_code: str | None = None,
    reason: str | None = None,
) -> CapacityObservation:
    if reserve_bytes < 0:
        raise ValueError("reserve_bytes must be non-negative")
    when = _utc(as_of)
    if unavailable:
        return CapacityObservation(
            "unavailable",
            None,
            None,
            reserve_bytes,
            estimated_next_backup_bytes,
            None,
            when,
            reason_code or "capacity_unavailable",
            reason,
        )
    if total_bytes is None or available_bytes is None:
        return CapacityObservation(
            "unknown",
            total_bytes,
            available_bytes,
            reserve_bytes,
            estimated_next_backup_bytes,
            None,
            when,
            reason_code or "capacity_unknown",
            reason,
        )
    required = reserve_bytes + (estimated_next_backup_bytes or 0)
    insufficient = (
        available_bytes < required
        if estimated_next_backup_bytes is not None
        else available_bytes <= reserve_bytes
    )
    if insufficient:
        status: Literal["ok", "warning", "insufficient"] = "insufficient"
        code = reason_code or "capacity_insufficient"
        acceptable = False
    else:
        warning_buffer = max(reserve_bytes, estimated_next_backup_bytes or 0)
        warning = warning_buffer > 0 and available_bytes < required + warning_buffer
        status = "warning" if warning else "ok"
        code = reason_code or ("capacity_warning" if warning else "capacity_ok")
        acceptable = True if estimated_next_backup_bytes is not None else None
    return CapacityObservation(
        status,
        total_bytes,
        available_bytes,
        reserve_bytes,
        estimated_next_backup_bytes,
        acceptable,
        when,
        code,
        reason,
    )


def query_local_capacity(
    path: str,
    *,
    reserve_bytes: int,
    estimated_next_backup_bytes: int | None,
    as_of: datetime | None = None,
) -> CapacityObservation:
    when = _utc(as_of or datetime.now(UTC))
    try:
        info = os.statvfs(path)
    except FileNotFoundError:
        return capacity_observation(
            total_bytes=None,
            available_bytes=None,
            reserve_bytes=reserve_bytes,
            estimated_next_backup_bytes=estimated_next_backup_bytes,
            as_of=when,
            unavailable=True,
            reason_code="local_destination_unavailable",
            reason="The local backup destination is unavailable.",
        )
    except (AttributeError, NotImplementedError):
        return capacity_observation(
            total_bytes=None,
            available_bytes=None,
            reserve_bytes=reserve_bytes,
            estimated_next_backup_bytes=estimated_next_backup_bytes,
            as_of=when,
            reason_code="capacity_unsupported",
            reason="Capacity information is not available for this destination.",
        )
    except OSError:
        return capacity_observation(
            total_bytes=None,
            available_bytes=None,
            reserve_bytes=reserve_bytes,
            estimated_next_backup_bytes=estimated_next_backup_bytes,
            as_of=when,
            unavailable=True,
            reason_code="local_capacity_unavailable",
            reason="Local backup capacity could not be queried.",
        )
    unit = int(info.f_frsize)
    return capacity_observation(
        total_bytes=int(info.f_blocks) * unit,
        available_bytes=int(info.f_bavail) * unit,
        reserve_bytes=reserve_bytes,
        estimated_next_backup_bytes=estimated_next_backup_bytes,
        as_of=when,
    )


def atomic_promote_local_archive(
    backup_dir: str,
    job_id: str,
    ciphertext: bytes,
    lease: BackupOperationLease,
) -> str:
    """Write/fsync a same-directory partial, then promote only while lock-owned."""
    base = Path(backup_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    filename = _managed_local_name(job_id)
    final_path = resolve_managed_local_path(str(base), filename, expected_job_id=job_id)
    partial_path = base / f"{filename}.partial"
    promoted = False
    created_partial = False
    try:
        if final_path.exists():
            raise LocalArchiveConflictError("backup archive already exists")
        with partial_path.open("xb") as handle:
            created_partial = True
            handle.write(ciphertext)
            handle.flush()
            os.fsync(handle.fileno())
        lease.assert_owned()
        if final_path.exists():
            raise LocalArchiveConflictError("backup archive already exists")
        os.replace(partial_path, final_path)
        promoted = True
        try:
            descriptor = os.open(base, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
        return filename
    finally:
        if created_partial and not promoted:
            try:
                partial_path.unlink(missing_ok=True)
            except OSError:
                pass
