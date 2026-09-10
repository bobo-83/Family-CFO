"""Userspace SMB storage primitives for encrypted off-box backups.

The password is handled as a secret: encrypted at rest by the caller and never
included in responses or logs.  All smbclient calls share one process-local,
re-entrant gate because smbclient's connection cache is process-global.
"""

from __future__ import annotations

import logging
import re
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, TypedDict

import smbclient
from smbprotocol.exceptions import SMBOSError, SMBResponseException

logger = logging.getLogger(__name__)

REMOTE_READ_PROBE_LIMIT = 32
_COPY_CHUNK_BYTES = 1024 * 1024
_ARCHIVE_RE = re.compile(
    r"^(?P<job_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
    r"(?:\.v(?P<version>[0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?))?\.enc$"
)


@dataclass(frozen=True, slots=True)
class SmbTarget:
    host: str
    share: str
    folder: str | None
    username: str
    password: str
    domain: str | None = None


class SmbClientGate:
    """Serialize smbclient cache ownership inside one process.

    The gate is deliberately re-entrant so a destructive helper can perform its
    mandatory read probe without releasing ownership before deletion.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

    @contextmanager
    def hold(self) -> Iterator[None]:
        with self._lock:
            yield


SMB_CLIENT_GATE = SmbClientGate()


class SmbStorageError(RuntimeError):
    """A stable, redacted SMB failure safe to expose to an operator."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class SmbInventoryError(SmbStorageError):
    pass


class SmbReadProbeError(SmbStorageError):
    pass


class SmbCapacityError(SmbStorageError):
    pass


class SmbUploadConflictError(SmbStorageError):
    pass


class RemoteBackupItem(TypedDict):
    """Compatibility shape consumed by the existing processing and API callers."""

    filename: str
    size_bytes: int
    modified_at: int
    app_version: str | None
    job_id: str
    timestamp_source: Literal["remote_modified_at"]


@dataclass(frozen=True, slots=True)
class SmbInventoryItem:
    filename: str
    job_id: str
    app_version: str | None
    size_bytes: int
    modified_at: int
    timestamp_source: Literal["remote_modified_at"] = "remote_modified_at"
    readable_candidate: bool = True
    anomaly_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SmbProtectedEntry:
    filename: str
    size_bytes: int | None
    modified_at: int | None
    anomaly_code: Literal["partial_file", "unrecognized_encrypted_file", "non_regular_file"]


@dataclass(frozen=True, slots=True)
class SmbInventory:
    """A successfully queried inventory; an unavailable share raises instead."""

    items: tuple[SmbInventoryItem, ...]
    protected_entries: tuple[SmbProtectedEntry, ...]


@dataclass(frozen=True, slots=True)
class SmbReadProbeResult:
    status: Literal["complete", "partial", "unavailable"]
    probed_archive_count: int
    readable_archive_count: int | None
    oldest_readable: SmbInventoryItem | None
    newest_readable: SmbInventoryItem | None


@dataclass(frozen=True, slots=True)
class SmbCapacity:
    total_bytes: int
    available_bytes: int
    actual_available_bytes: int


def _unc_base(target: SmbTarget) -> str:
    base = f"\\\\{target.host}\\{target.share}"
    if target.folder:
        cleaned = target.folder.strip("\\/").replace("/", "\\")
        if cleaned:
            base += "\\" + cleaned
    return base


def _open(target: SmbTarget) -> None:
    # smbprotocol takes an AD/NT domain as DOMAIN\\user; home Synology setups
    # (WORKGROUP) just use the bare username.
    username = target.username
    if target.domain and "\\" not in username:
        username = f"{target.domain}\\{username}"
    smbclient.register_session(target.host, username=username, password=target.password)


def _classify(exc: Exception) -> tuple[str, str]:
    """Map only allowlisted signals to stable text; never echo the exception."""
    text = str(exc).lower()
    if (
        isinstance(exc, NotImplementedError)
        or "not supported" in text
        or "invalid device request" in text
    ):
        return (
            "capacity_unsupported",
            "Capacity information is not available for this destination.",
        )
    if "logon" in text or "password" in text or "credential" in text or "access is denied" in text:
        return "authentication_failed", "The Synology rejected the username or password."
    if "bad_network_name" in text or "network name" in text or "share name" in text:
        return "share_not_found", "That share name wasn't found on the Synology."
    if (
        "timed out" in text
        or "refused" in text
        or "unreachable" in text
        or "name or service" in text
    ):
        return (
            "destination_unreachable",
            "Couldn't reach the Synology at that address — check the IP and that SMB is enabled.",
        )
    return "smb_unavailable", "The Synology operation failed. Check the destination and try again."


def _friendly(exc: Exception) -> str:
    """Compatibility helper returning only redacted, allowlisted operator text."""
    return _classify(exc)[1]


def _safe_reset() -> None:
    try:
        smbclient.reset_connection_cache()
    except Exception as exc:  # noqa: BLE001 - cleanup must not replace the primary result
        code, _ = _classify(exc)
        logger.warning(
            "smb connection cache reset failed error_type=%s code=%s", type(exc).__name__, code
        )


def _parse_archive_name(filename: str) -> tuple[str, str | None] | None:
    match = _ARCHIVE_RE.fullmatch(filename)
    if match is None:
        return None
    return match.group("job_id"), match.group("version")


def _require_archive_name(filename: str) -> None:
    if _parse_archive_name(filename) is None:
        raise ValueError("filename is not a recognized Family CFO backup archive")


def verify(target: SmbTarget) -> tuple[bool, str | None]:
    """Connect and prove we can write to the folder. Returns (ok, redacted reason)."""
    with SMB_CLIENT_GATE.hold():
        try:
            _open(target)
            base = _unc_base(target)
            try:
                smbclient.makedirs(base, exist_ok=True)
            except (SMBOSError, SMBResponseException, ValueError):
                pass  # folder may already exist, or the share root is the target
            probe = base + "\\.family-cfo-write-test"
            with smbclient.open_file(probe, mode="wb") as handle:
                handle.write(b"ok")
            smbclient.remove(probe)
            return True, None
        except Exception as exc:  # noqa: BLE001 - converted to a safe user-facing reason
            code, reason = _classify(exc)
            logger.warning(
                "smb verification failed error_type=%s code=%s", type(exc).__name__, code
            )
            return False, reason
        finally:
            _safe_reset()


def upload(target: SmbTarget, local_path: str, filename: str) -> None:
    """Atomically upload a recognized archive through a same-share partial file."""
    _require_archive_name(filename)
    with SMB_CLIENT_GATE.hold():
        partial_path: str | None = None
        created_partial = False
        try:
            _open(target)
            base = _unc_base(target)
            try:
                smbclient.makedirs(base, exist_ok=True)
            except (SMBOSError, SMBResponseException, ValueError):
                pass
            final_path = base + "\\" + filename
            partial_path = final_path + ".partial"
            if smbclient.path.exists(final_path) or smbclient.path.exists(partial_path):
                raise SmbUploadConflictError(
                    "archive_conflict", "A backup with this archive name already exists."
                )
            with open(local_path, "rb") as src, smbclient.open_file(partial_path, mode="xb") as dst:
                created_partial = True
                while chunk := src.read(_COPY_CHUNK_BYTES):
                    dst.write(chunk)
            smbclient.rename(partial_path, final_path)
            partial_path = None
        except Exception as exc:
            if partial_path is not None and created_partial:
                try:
                    if smbclient.path.exists(partial_path):
                        smbclient.remove(partial_path)
                except Exception as cleanup_exc:  # noqa: BLE001 - best-effort partial cleanup
                    code, _ = _classify(cleanup_exc)
                    logger.warning(
                        "smb partial cleanup failed error_type=%s code=%s",
                        type(cleanup_exc).__name__,
                        code,
                    )
            if isinstance(exc, SmbStorageError):
                raise
            code, reason = _classify(exc)
            logger.warning("smb upload failed error_type=%s code=%s", type(exc).__name__, code)
            raise SmbStorageError(code, reason) from None
        finally:
            _safe_reset()


def list_inventory(target: SmbTarget) -> SmbInventory:
    """Return a strict, stable inventory or raise a typed unavailable error."""
    with SMB_CLIENT_GATE.hold():
        try:
            _open(target)
            base = _unc_base(target)
            items: list[SmbInventoryItem] = []
            protected: list[SmbProtectedEntry] = []
            for entry in smbclient.scandir(base):
                name = entry.name
                is_partial = name.endswith(".partial")
                parsed = _parse_archive_name(name)
                if parsed is None and not (name.endswith(".enc") or is_partial):
                    continue
                info = entry.stat()
                size_bytes = int(info.st_size)
                modified_at = int(info.st_mtime)
                if not stat.S_ISREG(info.st_mode):
                    protected.append(
                        SmbProtectedEntry(name, size_bytes, modified_at, "non_regular_file")
                    )
                    continue
                if is_partial:
                    protected.append(
                        SmbProtectedEntry(name, size_bytes, modified_at, "partial_file")
                    )
                    continue
                if parsed is None:
                    protected.append(
                        SmbProtectedEntry(
                            name, size_bytes, modified_at, "unrecognized_encrypted_file"
                        )
                    )
                    continue
                job_id, app_version = parsed
                anomalies = ("zero_byte",) if size_bytes == 0 else ()
                items.append(
                    SmbInventoryItem(
                        filename=name,
                        job_id=job_id,
                        app_version=app_version,
                        size_bytes=size_bytes,
                        modified_at=modified_at,
                        readable_candidate=size_bytes > 0,
                        anomaly_codes=anomalies,
                    )
                )
            items.sort(key=lambda item: (-item.modified_at, item.filename))
            protected.sort(
                key=lambda item: (
                    -(item.modified_at if item.modified_at is not None else -1),
                    item.filename,
                )
            )
            return SmbInventory(tuple(items), tuple(protected))
        except Exception as exc:
            if isinstance(exc, SmbInventoryError):
                raise
            code, reason = _classify(exc)
            logger.warning("smb inventory failed error_type=%s code=%s", type(exc).__name__, code)
            raise SmbInventoryError(code, reason) from None
        finally:
            _safe_reset()


def list_backups(target: SmbTarget) -> list[RemoteBackupItem]:
    """Compatibility inventory for existing callers; failures now raise explicitly."""
    return [
        RemoteBackupItem(
            filename=item.filename,
            size_bytes=item.size_bytes,
            modified_at=item.modified_at,
            app_version=item.app_version,
            job_id=item.job_id,
            timestamp_source=item.timestamp_source,
        )
        for item in list_inventory(target).items
        if item.readable_candidate
    ]


def _probe_file(target: SmbTarget, filename: str) -> bool:
    path = _unc_base(target) + "\\" + filename
    try:
        with smbclient.open_file(path, mode="rb") as handle:
            return handle.read(1) != b""
    except Exception:  # noqa: BLE001 - every read failure protects the archive from deletion
        return False


def probe_inventory(
    target: SmbTarget,
    inventory: SmbInventory | None = None,
    *,
    limit: int = REMOTE_READ_PROBE_LIMIT,
) -> SmbReadProbeResult:
    """Boundedly qualify oldest/newest readable endpoints, alternating sides."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    with SMB_CLIENT_GATE.hold():
        current = inventory if inventory is not None else list_inventory(target)
        candidates = tuple(item for item in current.items if item.readable_candidate)
        if not candidates:
            return SmbReadProbeResult("complete", 0, 0, None, None)

        results: dict[str, bool] = {}
        oldest: SmbInventoryItem | None = None
        newest: SmbInventoryItem | None = None
        oldest_order = tuple(reversed(candidates))
        newest_order = candidates
        choose_oldest = True

        try:
            try:
                _open(target)
            except Exception as exc:  # noqa: BLE001 - unavailable is an explicit probe state
                code, _ = _classify(exc)
                logger.warning("smb probe failed error_type=%s code=%s", type(exc).__name__, code)
                return SmbReadProbeResult("unavailable", 0, None, None, None)
            while len(results) < limit and (oldest is None or newest is None):
                order = oldest_order if choose_oldest else newest_order
                endpoint = oldest if choose_oldest else newest
                if endpoint is None:
                    for item in order:
                        if item.filename not in results:
                            results[item.filename] = _probe_file(target, item.filename)
                            if results[item.filename]:
                                if choose_oldest:
                                    oldest = item
                                else:
                                    newest = item
                            break
                        if results[item.filename]:
                            if choose_oldest:
                                oldest = item
                            else:
                                newest = item
                            break
                choose_oldest = not choose_oldest
                if all(item.filename in results for item in candidates):
                    break
        finally:
            _safe_reset()

        all_probed = len(results) == len(candidates)
        if all_probed:
            readable_count: int | None = sum(results.values())
            if oldest is None:
                oldest = next((item for item in oldest_order if results[item.filename]), None)
            if newest is None:
                newest = next((item for item in newest_order if results[item.filename]), None)
        else:
            readable_count = None
        if oldest is not None and newest is not None:
            status: Literal["complete", "partial", "unavailable"] = "complete"
        elif any(results.values()):
            status = "partial"
        else:
            status = "unavailable"
        return SmbReadProbeResult(status, len(results), readable_count, oldest, newest)


def query_capacity(target: SmbTarget) -> SmbCapacity:
    """Return SMB volume bytes available to this caller (quota-aware)."""
    with SMB_CLIENT_GATE.hold():
        try:
            _open(target)
            result = smbclient.stat_volume(_unc_base(target))
            return SmbCapacity(
                total_bytes=int(result.total_size),
                available_bytes=int(result.caller_available_size),
                actual_available_bytes=int(result.actual_available_size),
            )
        except Exception as exc:  # noqa: BLE001 - every vendor failure becomes a typed safe result
            code, reason = _classify(exc)
            logger.warning("smb capacity failed error_type=%s code=%s", type(exc).__name__, code)
            raise SmbCapacityError(code, reason) from None
        finally:
            _safe_reset()


def download(target: SmbTarget, filename: str) -> bytes:
    """Read one recognized archive back from the share for restore."""
    _require_archive_name(filename)
    with SMB_CLIENT_GATE.hold():
        try:
            _open(target)
            path = _unc_base(target) + "\\" + filename
            with smbclient.open_file(path, mode="rb") as handle:
                return handle.read()
        except Exception as exc:
            if isinstance(exc, SmbStorageError):
                raise
            code, reason = _classify(exc)
            logger.warning("smb download failed error_type=%s code=%s", type(exc).__name__, code)
            raise SmbStorageError(code, reason) from None
        finally:
            _safe_reset()


def delete(target: SmbTarget, filename: str) -> None:
    """Read-probe and remove one recognized archive from the share."""
    _require_archive_name(filename)
    with SMB_CLIENT_GATE.hold():
        try:
            _open(target)
            if not _probe_file(target, filename):
                raise SmbReadProbeError(
                    "archive_read_probe_failed",
                    "The backup could not be read, so it was not deleted.",
                )
            smbclient.remove(_unc_base(target) + "\\" + filename)
        except Exception as exc:
            if isinstance(exc, SmbStorageError):
                raise
            code, reason = _classify(exc)
            logger.warning("smb delete failed error_type=%s code=%s", type(exc).__name__, code)
            raise SmbStorageError(code, reason) from None
        finally:
            _safe_reset()
