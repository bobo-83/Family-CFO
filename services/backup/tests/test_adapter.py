from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from family_cfo_backup import (
    BackupCommandError,
    BackupCommandTimeoutError,
    PgDumpBackupAdapter,
    SqliteFileBackupAdapter,
)


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stderr = stderr


def test_pg_dump_adapter_strips_sqlalchemy_driver_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(
        args: list[str], capture_output: bool, check: bool, timeout: float
    ) -> _FakeCompletedProcess:
        captured["args"] = args
        captured["timeout"] = [str(timeout)]
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = PgDumpBackupAdapter(
        "postgresql+psycopg://family_cfo:family_cfo@localhost:5432/family_cfo"
    )
    adapter.dump_database(Path("/tmp/backup.dump"))

    assert captured["args"][0] == "pg_dump"
    assert captured["args"][-1] == "postgresql://family_cfo:family_cfo@localhost:5432/family_cfo"
    assert captured["timeout"] == ["3600"]


def test_pg_dump_adapter_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        args: list[str], capture_output: bool, check: bool, timeout: float
    ) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=1, stderr=b"pg_dump: connection refused")

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = PgDumpBackupAdapter("postgresql://family_cfo:family_cfo@localhost:5432/family_cfo")

    with pytest.raises(BackupCommandError) as exc_info:
        adapter.dump_database(Path("/tmp/backup.dump"))

    assert exc_info.value.command == "pg_dump"
    assert "connection refused" in exc_info.value.stderr


def test_pg_restore_adapter_raises_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        args: list[str], capture_output: bool, check: bool, timeout: float
    ) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=1, stderr=b"pg_restore: could not connect")

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = PgDumpBackupAdapter("postgresql://family_cfo:family_cfo@localhost:5432/family_cfo")

    with pytest.raises(BackupCommandError) as exc_info:
        adapter.restore_database(Path("/tmp/backup.dump"))

    assert exc_info.value.command == "pg_restore"


@pytest.mark.parametrize(
    ("operation", "command"),
    [("dump_database", "pg_dump"), ("restore_database", "pg_restore")],
)
def test_pg_adapter_raises_typed_timeout(
    monkeypatch: pytest.MonkeyPatch, operation: str, command: str
) -> None:
    def fake_run(
        args: list[str], capture_output: bool, check: bool, timeout: float
    ) -> _FakeCompletedProcess:
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = PgDumpBackupAdapter(
        "postgresql://family_cfo:family_cfo@localhost:5432/family_cfo",
        timeout_seconds=12.5,
    )

    with pytest.raises(BackupCommandTimeoutError) as exc_info:
        getattr(adapter, operation)(Path("/tmp/backup.dump"))

    assert isinstance(exc_info.value, BackupCommandError)
    assert exc_info.value.command == command
    assert exc_info.value.timeout_seconds == 12.5


def test_pg_adapter_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        PgDumpBackupAdapter("postgresql://localhost/family_cfo", timeout_seconds=0)


def test_sqlite_file_backup_adapter_dump_restore_round_trip(tmp_path: Path) -> None:
    database_path = tmp_path / "family_cfo.sqlite"
    database_path.write_bytes(b"original database bytes")

    adapter = SqliteFileBackupAdapter(database_path)
    dump_path = tmp_path / "backup.dump"
    adapter.dump_database(dump_path)

    assert dump_path.read_bytes() == b"original database bytes"

    database_path.write_bytes(b"mutated database bytes")
    adapter.restore_database(dump_path)

    assert database_path.read_bytes() == b"original database bytes"
