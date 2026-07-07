from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["FAMILY_CFO_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_migrations_upgrade_and_downgrade_cleanly(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration_rollback_test.db'}"

    upgraded = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgraded.returncode == 0, upgraded.stderr

    downgraded = _run_alembic("downgrade", "base", database_url=database_url)
    assert downgraded.returncode == 0, downgraded.stderr

    re_upgraded = _run_alembic("upgrade", "head", database_url=database_url)
    assert re_upgraded.returncode == 0, re_upgraded.stderr
