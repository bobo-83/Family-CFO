from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from family_cfo_api.backup_operation_lock import (
    BackupOperationLockLostError,
    acquire_backup_operation_lock,
    try_acquire_backup_operation_lock,
)


class _ScalarResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value


class _FakePostgresConnection:
    def __init__(self, *, acquire: bool = True, owned: bool = True) -> None:
        self.acquire = acquire
        self.owned = owned
        self.closed = False
        self.invalidated = False
        self.statements: list[str] = []

    def execution_options(self, **kwargs):
        assert kwargs == {"isolation_level": "AUTOCOMMIT"}
        return self

    def execute(self, statement, parameters=None) -> _ScalarResult:
        sql = str(statement)
        self.statements.append(sql)
        if "pg_try_advisory_lock" in sql:
            return _ScalarResult(self.acquire)
        if "pg_locks" in sql:
            return _ScalarResult(self.owned)
        if "pg_advisory_unlock" in sql:
            return _ScalarResult(True)
        raise AssertionError(sql)

    def close(self) -> None:
        self.closed = True


class _FakePostgresEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, connection: _FakePostgresConnection) -> None:
        self.connection = connection

    def connect(self) -> _FakePostgresConnection:
        return self.connection


def test_postgres_lease_uses_dedicated_session_and_checks_pg_locks() -> None:
    connection = _FakePostgresConnection()
    lease = acquire_backup_operation_lock(_FakePostgresEngine(connection))  # type: ignore[arg-type]

    lease.assert_owned()
    lease.release()

    assert any("pg_try_advisory_lock" in sql for sql in connection.statements)
    assert any("pg_locks" in sql and "pg_backend_pid" in sql for sql in connection.statements)
    assert any("pg_advisory_unlock" in sql for sql in connection.statements)
    assert connection.closed is True


def test_postgres_busy_connection_is_closed_without_a_lease() -> None:
    connection = _FakePostgresConnection(acquire=False)

    assert (
        try_acquire_backup_operation_lock(_FakePostgresEngine(connection))  # type: ignore[arg-type]
        is None
    )
    assert connection.closed is True


def test_postgres_ownership_loss_aborts_destructive_phase() -> None:
    connection = _FakePostgresConnection(owned=False)
    lease = acquire_backup_operation_lock(_FakePostgresEngine(connection))  # type: ignore[arg-type]
    try:
        with pytest.raises(BackupOperationLockLostError):
            lease.assert_owned()
    finally:
        lease.release()


def test_sqlite_lock_conflicts_across_engines_for_same_database(
    demo_file_engine: Engine,
) -> None:
    second = create_engine(str(demo_file_engine.url))
    first_lease = acquire_backup_operation_lock(demo_file_engine)
    try:
        assert try_acquire_backup_operation_lock(second) is None
    finally:
        first_lease.release()
        second.dispose()


def test_sqlite_lock_can_be_reacquired_after_release(demo_file_engine: Engine) -> None:
    first = acquire_backup_operation_lock(demo_file_engine)
    first.release()

    second = acquire_backup_operation_lock(demo_file_engine)
    second.assert_owned()
    second.release()


def test_sqlite_lease_reports_loss_after_release(demo_file_engine: Engine) -> None:
    lease = acquire_backup_operation_lock(demo_file_engine)
    lease.release()

    with pytest.raises(BackupOperationLockLostError):
        lease.assert_owned()
