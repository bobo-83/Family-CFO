"""Durable manager journal (ADR 0081 S3) - the execution authority.

Layout under PROJECT_DIR/.runtime-swap/v2/:

    requests/<request_id>.json          request-ID tombstones
    jobs/<job_id>/record.json           schema-2 durable job record
    jobs/<job_id>/previous.json         immutable previous-deployment snapshot
    jobs/<job_id>/target.json           fully materialized frozen target plan
    jobs/<job_id>/diagnostics.log       private bounded diagnostics
    current.json                        current-job pointer + monotonic counter
    journal.lock                        short journal-file lock

Writes are temp file -> fsync -> atomic rename -> directory fsync; directories
are 0700 and files 0600. Corruption, path traversal, and unsupported schema
versions fail closed with runtime_state_corrupt - never an automatic reset to
idle. Retention: unresolved jobs and rollback artifacts (previous.json)
indefinitely; terminal job records 30 days; request tombstones persist so
replays of expired-detail requests answer request_expired instead of
restarting old operations.

Importable without FastAPI/Pydantic (CLI shares this engine, ADR 0081 S10).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from family_cfo_model_manager.contracts import (
    JOURNAL_SCHEMA_VERSION,
    JobDecision,
    JobOrigin,
    JobRecord,
    JobState,
    ReasonCode,
)

TERMINAL_RETENTION = timedelta(days=30)
DIAGNOSTICS_ROTATE_BYTES = 1 << 20  # private diagnostics rotate at 1 MiB
DIAGNOSTICS_KEEP_BYTES = 512 << 10

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class JournalError(RuntimeError):
    """Journal failure with a stable reason code."""

    def __init__(self, code: ReasonCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _validate_uuid(value: str, kind: str) -> str:
    """Strict canonical-UUID check; anything else fails closed.

    Path traversal (dot-dot), separators, and crafted names never reach the
    filesystem: they are corruption, not input.
    """
    lowered = value.strip().lower()
    if not _UUID_RE.match(lowered):
        raise JournalError(
            ReasonCode.RUNTIME_STATE_CORRUPT, f"invalid {kind} identifier"
        )
    return lowered


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _from_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass(frozen=True, slots=True)
class RequestTombstone:
    """One admitted request identity, kept so replays never restart work."""

    request_id: str
    request_hash: str
    job_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CurrentPointer:
    """The current-job pointer document, plus the monotonic sequence counter."""

    sequence: int
    job_id: str | None
    updated_at: datetime | None


class Journal:
    """Atomic, fail-closed persistence for apply jobs."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self._ensure_layout()

    # --- layout ---------------------------------------------------------------

    @property
    def jobs_dir(self) -> Path:
        return self.root / "jobs"

    @property
    def requests_dir(self) -> Path:
        return self.root / "requests"

    @property
    def lock_path(self) -> Path:
        return self.root / "journal.lock"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / _validate_uuid(job_id, "job")

    def request_path(self, request_id: str) -> Path:
        return self.requests_dir / (_validate_uuid(request_id, "request") + ".json")

    def _ensure_layout(self) -> None:
        for directory in (self.root, self.jobs_dir, self.requests_dir):
            self._ensure_dir(directory)

    @staticmethod
    def _ensure_dir(directory: Path) -> None:
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        except OSError as exc:
            raise JournalError(
                ReasonCode.JOURNAL_UNAVAILABLE,
                f"could not prepare the journal directory: {exc}",
            ) from exc

    def locked(self) -> _JournalLock:
        """The short journal-file lock. Hold it across multi-step mutations and
        release it BEFORE waiting on deployment ownership (ADR 0081 S5 - no
        lock-order inversion)."""
        return _JournalLock(self.lock_path)

    # --- atomic primitives ------------------------------------------------------

    def _atomic_write(self, path: Path, payload: bytes) -> None:
        if path.is_symlink():
            raise JournalError(
                ReasonCode.RUNTIME_STATE_CORRUPT,
                f"refusing to write through a symlink: {path.name}",
            )
        directory = path.parent
        try:
            self._ensure_dir(directory)
            fd, temp_name = tempfile.mkstemp(dir=directory, prefix=".tmp-")
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except JournalError:
            raise
        except OSError as exc:
            raise JournalError(
                ReasonCode.JOURNAL_UNAVAILABLE,
                f"could not persist {path.name}: {exc}",
            ) from exc

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if path.is_symlink():
            raise JournalError(
                ReasonCode.RUNTIME_STATE_CORRUPT,
                f"refusing to read through a symlink: {path.name}",
            )
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise JournalError(
                ReasonCode.JOURNAL_UNAVAILABLE, f"could not read {path.name}: {exc}"
            ) from exc
        try:
            document = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise JournalError(
                ReasonCode.RUNTIME_STATE_CORRUPT, f"{path.name} is corrupt"
            ) from exc
        if not isinstance(document, dict):
            raise JournalError(
                ReasonCode.RUNTIME_STATE_CORRUPT, f"{path.name} is corrupt"
            )
        return document

    def _require_schema(self, document: dict[str, Any], path_name: str) -> None:
        version = document.get("schema_version")
        if version != JOURNAL_SCHEMA_VERSION:
            raise JournalError(
                ReasonCode.RUNTIME_STATE_CORRUPT,
                f"{path_name} has unsupported schema version {version!r}",
            )

    # --- job records --------------------------------------------------------------

    def write_job_record(self, record: JobRecord) -> None:
        if record.schema_version != JOURNAL_SCHEMA_VERSION:
            raise JournalError(
                ReasonCode.RUNTIME_STATE_CORRUPT,
                f"unsupported record schema {record.schema_version!r}",
            )
        path = self.job_dir(record.job_id) / "record.json"
        self._atomic_write(path, json.dumps(_record_to_dict(record)).encode())

    def read_job_record(self, job_id: str) -> JobRecord | None:
        document = self._read_json(self.job_dir(job_id) / "record.json")
        if document is None:
            return None
        self._require_schema(document, "record.json")
        return _record_from_dict(document)

    def job_ids(self) -> tuple[str, ...]:
        try:
            entries = sorted(self.jobs_dir.iterdir())
        except OSError as exc:
            raise JournalError(
                ReasonCode.JOURNAL_UNAVAILABLE, f"could not list jobs: {exc}"
            ) from exc
        # Anything that is not a canonical UUID is corruption, not data.
        return tuple(
            _validate_uuid(entry.name, "job") for entry in entries if entry.is_dir()
        )

    def list_job_records(self) -> tuple[JobRecord, ...]:
        records = []
        for job_id in self.job_ids():
            record = self.read_job_record(job_id)
            if record is not None:
                records.append(record)
        return tuple(sorted(records, key=lambda item: item.sequence))

    # --- snapshots / target plans / diagnostics -----------------------------------

    def write_previous_snapshot(self, job_id: str, snapshot: dict[str, Any]) -> None:
        self._atomic_write(
            self.job_dir(job_id) / "previous.json", json.dumps(snapshot).encode()
        )

    def read_previous_snapshot(self, job_id: str) -> dict[str, Any] | None:
        return self._read_json(self.job_dir(job_id) / "previous.json")

    def write_target_plan(self, job_id: str, plan: dict[str, Any]) -> None:
        self._atomic_write(
            self.job_dir(job_id) / "target.json", json.dumps(plan).encode()
        )

    def read_target_plan(self, job_id: str) -> dict[str, Any] | None:
        return self._read_json(self.job_dir(job_id) / "target.json")

    def append_diagnostics(self, job_id: str, line: str) -> None:
        """Append one bounded diagnostics line; rotate at 1 MiB (keep tail)."""
        path = self.job_dir(job_id) / "diagnostics.log"
        try:
            self._ensure_dir(path.parent)
            if path.exists() and path.stat().st_size >= DIAGNOSTICS_ROTATE_BYTES:
                self._atomic_write(path, path.read_bytes()[-DIAGNOSTICS_KEEP_BYTES:])
            with open(path, "a") as handle:
                handle.write(line.rstrip() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, 0o600)
        except OSError as exc:
            raise JournalError(
                ReasonCode.JOURNAL_UNAVAILABLE,
                f"could not append diagnostics: {exc}",
            ) from exc

    # --- request tombstones ---------------------------------------------------------

    def record_request(self, tombstone: RequestTombstone) -> None:
        payload = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "request_id": _validate_uuid(tombstone.request_id, "request"),
            "request_hash": tombstone.request_hash,
            "job_id": _validate_uuid(tombstone.job_id, "job"),
            "created_at": _iso(tombstone.created_at),
        }
        self._atomic_write(
            self.request_path(tombstone.request_id), json.dumps(payload).encode()
        )

    def read_request(self, request_id: str) -> RequestTombstone | None:
        document = self._read_json(self.request_path(request_id))
        if document is None:
            return None
        self._require_schema(document, "request tombstone")
        try:
            return RequestTombstone(
                request_id=_validate_uuid(document["request_id"], "request"),
                request_hash=str(document["request_hash"]),
                job_id=_validate_uuid(document["job_id"], "job"),
                created_at=_from_iso(document["created_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise JournalError(
                ReasonCode.RUNTIME_STATE_CORRUPT, "request tombstone is corrupt"
            ) from exc

    def job_for_request(self, request_id: str) -> JobRecord | None:
        """The admitted job for a request, or None.

        None with a surviving tombstone means the job detail was
        retention-pruned; replays must answer request_expired (see
        request_state).
        """
        tombstone = self.read_request(request_id)
        if tombstone is None:
            return None
        return self.read_job_record(tombstone.job_id)

    def request_state(self, request_id: str) -> JobRecord | None:
        """Replay resolution for admission: a live job record, or a raise.

        Raises request_expired only when the tombstone survives but its job
        detail has been pruned - the replay must not restart the old operation.
        """
        record = self.job_for_request(request_id)
        if record is not None:
            return record
        if self.read_request(request_id) is not None:
            raise JournalError(
                ReasonCode.REQUEST_EXPIRED,
                "the request's job detail is no longer retained",
            )
        return None

    # --- current pointer ---------------------------------------------------------

    def read_current(self) -> CurrentPointer:
        document = self._read_json(self.root / "current.json")
        if document is None:
            return CurrentPointer(sequence=0, job_id=None, updated_at=None)
        self._require_schema(document, "current.json")
        try:
            job_id = document.get("job_id")
            return CurrentPointer(
                sequence=int(document["sequence"]),
                job_id=_validate_uuid(job_id, "job") if job_id else None,
                updated_at=(
                    _from_iso(document["updated_at"])
                    if document.get("updated_at")
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise JournalError(
                ReasonCode.RUNTIME_STATE_CORRUPT, "current.json is corrupt"
            ) from exc

    def write_current(self, pointer: CurrentPointer) -> None:
        payload = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "sequence": pointer.sequence,
            "job_id": pointer.job_id,
            "updated_at": _iso(pointer.updated_at) if pointer.updated_at else None,
        }
        self._atomic_write(self.root / "current.json", json.dumps(payload).encode())

    def allocate_sequence(self) -> int:
        """The next globally monotonic sequence number (caller holds the lock)."""
        pointer = self.read_current()
        sequence = pointer.sequence + 1
        self.write_current(
            CurrentPointer(
                sequence=sequence, job_id=pointer.job_id, updated_at=datetime.now(UTC)
            )
        )
        return sequence

    def set_current_job(self, job_id: str | None) -> None:
        pointer = self.read_current()
        self.write_current(
            CurrentPointer(
                sequence=pointer.sequence,
                job_id=_validate_uuid(job_id, "job") if job_id else None,
                updated_at=datetime.now(UTC),
            )
        )

    # --- retention ------------------------------------------------------------------

    def prune(self, now: datetime) -> tuple[str, ...]:
        """Drop terminal job records past retention; keep rollback artifacts.

        Unresolved jobs and previous snapshots are kept indefinitely. A corrupt
        record fails closed - pruning never silently resets state.
        """
        pruned: list[str] = []
        for job_id in self.job_ids():
            record = self.read_job_record(job_id)
            if record is None:
                # previous.json may survive alone after an earlier prune.
                continue
            if not record.state.terminal:
                continue
            if now - record.updated_at < TERMINAL_RETENTION:
                continue
            directory = self.job_dir(job_id)
            for name in ("record.json", "target.json", "diagnostics.log"):
                try:
                    (directory / name).unlink(missing_ok=True)
                except OSError as exc:
                    raise JournalError(
                        ReasonCode.JOURNAL_UNAVAILABLE,
                        f"could not prune {name}: {exc}",
                    ) from exc
            try:
                directory.rmdir()  # succeeds only when nothing is retained
            except OSError:
                pass
            pointer = self.read_current()
            if pointer.job_id == job_id:
                self.set_current_job(None)
            pruned.append(job_id)
        return tuple(pruned)


class _JournalLock:
    """Advisory exclusive lock on the journal (flock; released on close)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> Self:
        self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def _record_to_dict(record: JobRecord) -> dict[str, Any]:
    return {
        "schema_version": record.schema_version,
        "job_id": record.job_id,
        "request_id": record.request_id,
        "sequence": record.sequence,
        "origin": record.origin.value,
        "runtime_key": record.runtime_key,
        "request_hash": record.request_hash,
        "state": record.state.value,
        "decision": record.decision.value,
        "created_at": _iso(record.created_at),
        "updated_at": _iso(record.updated_at),
        "initiator_household_id": record.initiator_household_id,
        "initiator_user_id": record.initiator_user_id,
        "phase_deadline_at": (
            _iso(record.phase_deadline_at) if record.phase_deadline_at else None
        ),
        "elapsed_seconds": record.elapsed_seconds,
        "failure_code": record.failure_code.value if record.failure_code else None,
        "safe_message": record.safe_message,
        "previous_snapshot_hash": record.previous_snapshot_hash,
        "target_plan_hash": record.target_plan_hash,
        "config_receipt_id": record.config_receipt_id,
    }


def _record_from_dict(document: dict[str, Any]) -> JobRecord:
    try:
        return JobRecord(
            schema_version=int(document["schema_version"]),
            job_id=_validate_uuid(document["job_id"], "job"),
            request_id=_validate_uuid(document["request_id"], "request"),
            sequence=int(document["sequence"]),
            origin=JobOrigin(document["origin"]),
            runtime_key=str(document["runtime_key"]),
            request_hash=str(document["request_hash"]),
            state=JobState(document["state"]),
            decision=JobDecision(document["decision"]),
            created_at=_from_iso(document["created_at"]),
            updated_at=_from_iso(document["updated_at"]),
            initiator_household_id=document.get("initiator_household_id"),
            initiator_user_id=document.get("initiator_user_id"),
            phase_deadline_at=(
                _from_iso(document["phase_deadline_at"])
                if document.get("phase_deadline_at")
                else None
            ),
            elapsed_seconds=(
                float(document["elapsed_seconds"])
                if document.get("elapsed_seconds") is not None
                else None
            ),
            failure_code=(
                ReasonCode(document["failure_code"])
                if document.get("failure_code")
                else None
            ),
            safe_message=document.get("safe_message"),
            previous_snapshot_hash=document.get("previous_snapshot_hash"),
            target_plan_hash=document.get("target_plan_hash"),
            config_receipt_id=document.get("config_receipt_id"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise JournalError(
            ReasonCode.RUNTIME_STATE_CORRUPT, "record.json is corrupt"
        ) from exc
