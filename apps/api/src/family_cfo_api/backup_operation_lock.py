"""Cross-process ownership for every backup-storage mutation.

PostgreSQL uses one session advisory lock held by a dedicated connection. SQLite
is supported only as an explicit process-local test seam, keyed by the backing
file so distinct Engines for the same fixture still contend.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

# Two signed 32-bit keys avoid relying on backend-specific bigint decoding.
_ADVISORY_CLASS_ID = 0x4643464F  # "FCFO"
_ADVISORY_OBJECT_ID = 0x4241434B  # "BACK"


class BackupOperationBusyError(RuntimeError):
    """Another process owns the box-global backup mutation lock."""


class BackupOperationLockLostError(RuntimeError):
    """The dedicated PostgreSQL session no longer owns its advisory lock."""


class BackupOperationLease(Protocol):
    def assert_owned(self) -> None: ...

    def release(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...


@dataclass(slots=True)
class _PostgresLease:
    connection: Connection
    released: bool = False

    def assert_owned(self) -> None:
        if self.released or self.connection.closed or self.connection.invalidated:
            raise BackupOperationLockLostError("backup operation lock was lost")
        try:
            owned = self.connection.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_locks "
                    "WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
                    "AND classid = CAST(:class_id AS oid) "
                    "AND objid = CAST(:object_id AS oid) "
                    "AND objsubid = 2 AND granted"
                    ")"
                ),
                {"class_id": _ADVISORY_CLASS_ID, "object_id": _ADVISORY_OBJECT_ID},
            ).scalar_one()
        except Exception as exc:  # connection loss is the safety signal
            raise BackupOperationLockLostError("backup operation lock was lost") from exc
        if not owned:
            raise BackupOperationLockLostError("backup operation lock was lost")

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            if not self.connection.closed and not self.connection.invalidated:
                self.connection.execute(
                    text("SELECT pg_advisory_unlock(:class_id, :object_id)"),
                    {"class_id": _ADVISORY_CLASS_ID, "object_id": _ADVISORY_OBJECT_ID},
                )
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask operation result
            logger.warning("backup advisory lock release failed error_type=%s", type(exc).__name__)
        finally:
            try:
                self.connection.close()
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask operation result
                logger.warning(
                    "backup advisory connection close failed error_type=%s",
                    type(exc).__name__,
                )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


@dataclass(slots=True)
class _SqliteLease:
    lock: threading.Lock
    released: bool = False

    def assert_owned(self) -> None:
        if self.released:
            raise BackupOperationLockLostError("backup operation lock was lost")

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


_SQLITE_LOCKS_GUARD = threading.Lock()
_SQLITE_LOCKS: dict[str, threading.Lock] = {}


def _sqlite_lock_key(engine: Engine) -> str:
    database = engine.url.database
    if database in (None, "", ":memory:"):
        return f"memory:{id(engine)}"
    return os.path.realpath(Path(database).expanduser().absolute())


def reset_sqlite_backup_operation_locks_for_tests() -> None:
    """Clear only unowned test locks; fixtures call this between test cases."""
    with _SQLITE_LOCKS_GUARD:
        if any(lock.locked() for lock in _SQLITE_LOCKS.values()):
            raise RuntimeError("cannot reset an owned backup operation lock")
        _SQLITE_LOCKS.clear()


def try_acquire_backup_operation_lock(engine: Engine) -> BackupOperationLease | None:
    """Try once, without blocking, and return the operation-owned lease."""
    dialect = engine.dialect.name
    if dialect == "postgresql":
        connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:class_id, :object_id)"),
                {"class_id": _ADVISORY_CLASS_ID, "object_id": _ADVISORY_OBJECT_ID},
            ).scalar_one()
        except Exception:
            connection.close()
            raise
        if not acquired:
            connection.close()
            return None
        return _PostgresLease(connection)
    if dialect == "sqlite":
        key = _sqlite_lock_key(engine)
        with _SQLITE_LOCKS_GUARD:
            lock = _SQLITE_LOCKS.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False):
            return None
        return _SqliteLease(lock)
    raise RuntimeError(f"backup operation locking is unsupported for {dialect}")


def acquire_backup_operation_lock(engine: Engine) -> BackupOperationLease:
    lease = try_acquire_backup_operation_lock(engine)
    if lease is None:
        raise BackupOperationBusyError("a backup operation is already in progress")
    return lease
