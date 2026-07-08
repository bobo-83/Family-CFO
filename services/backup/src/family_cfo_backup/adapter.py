from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Protocol


class BackupAdapter(Protocol):
    """The replaceable seam between the backup service and a database backend (ADR 0007)."""

    def dump_database(self, destination: Path) -> None: ...

    def restore_database(self, source: Path) -> None: ...


class BackupCommandError(RuntimeError):
    def __init__(self, command: str, returncode: int, stderr: str) -> None:
        super().__init__(f"{command} exited with code {returncode}")
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


def _to_libpq_url(database_url: str) -> str:
    """Strip a SQLAlchemy `+driver` suffix (e.g. `postgresql+psycopg://`) for libpq CLI tools."""
    scheme, separator, rest = database_url.partition("://")
    if not separator:
        return database_url
    return f"{scheme.split('+')[0]}://{rest}"


class PgDumpBackupAdapter:
    """Real adapter: shells out to `pg_dump`/`pg_restore` against a PostgreSQL database_url.

    This sandboxed development environment has no `pg_dump`/`pg_restore`
    binary and no live PostgreSQL server, so this adapter is only
    unit/contract-tested here (command construction, error handling) with a
    stubbed subprocess call -- the same "test the seam, not the vendor
    binary" approach M4 used for the vLLM HTTP layer and M7 used for OCR.
    """

    def __init__(
        self,
        database_url: str,
        *,
        pg_dump_path: str = "pg_dump",
        pg_restore_path: str = "pg_restore",
    ) -> None:
        self._connection_url = _to_libpq_url(database_url)
        self._pg_dump_path = pg_dump_path
        self._pg_restore_path = pg_restore_path

    def dump_database(self, destination: Path) -> None:
        result = subprocess.run(
            [self._pg_dump_path, "--format=custom", "--file", str(destination), self._connection_url],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise BackupCommandError(
                self._pg_dump_path, result.returncode, result.stderr.decode("utf-8", errors="replace")
            )

    def restore_database(self, source: Path) -> None:
        result = subprocess.run(
            [
                self._pg_restore_path,
                "--clean",
                "--if-exists",
                "--dbname",
                self._connection_url,
                str(source),
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise BackupCommandError(
                self._pg_restore_path, result.returncode, result.stderr.decode("utf-8", errors="replace")
            )


class SqliteFileBackupAdapter:
    """Test-only adapter: file-copies a file-based SQLite database.

    Never used against a `:memory:` URL -- there is no file to copy. It
    exercises the identical dump/restore seam as `PgDumpBackupAdapter`
    against a real file on disk, so encryption/retention/restore behavior is
    covered without a PostgreSQL server.
    """

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def dump_database(self, destination: Path) -> None:
        shutil.copyfile(self._database_path, destination)

    def restore_database(self, source: Path) -> None:
        shutil.copyfile(source, self._database_path)
