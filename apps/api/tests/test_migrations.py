from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

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


@pytest.mark.parametrize(
    ("offbox_max_bytes", "expected_legacy_max_bytes"),
    [(1000, 1000), (2000, None)],
)
def test_0093_schema_and_downgrade_preserve_representable_settings(
    tmp_path,
    offbox_max_bytes: int,
    expected_legacy_max_bytes: int | None,
) -> None:
    database_url = f"sqlite:///{tmp_path / f'backup_settings_0093_{offbox_max_bytes}.db'}"
    upgraded = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgraded.returncode == 0, upgraded.stderr

    engine = create_engine(database_url)
    try:
        schema = inspect(engine)
        assert "backup_settings" in schema.get_table_names()
        assert "backup_retention_events" in schema.get_table_names()
        assert {column["name"] for column in schema.get_columns("backup_jobs")} >= {
            "prune_reason"
        }
        assert not schema.get_foreign_keys("backup_settings")
        assert not schema.get_foreign_keys("backup_retention_events")

        now = "2026-09-10 12:00:00+00:00"
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO households (
                        id, display_name, base_currency, backup_frequency,
                        created_at, updated_at
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000001',
                        'Legacy', 'USD', 'daily', :now, :now
                    )
                    """
                ),
                {"now": now},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO backup_settings (
                        key, frequency, smb_host, smb_share, smb_folder,
                        smb_username, smb_password_encrypted, smb_domain,
                        local_retention_mode, local_keep_all_days,
                        local_daily_until_days, local_weekly_until_days,
                        offbox_retention_mode, offbox_keep_all_days,
                        offbox_daily_until_days, offbox_weekly_until_days,
                        local_max_bytes, offbox_max_bytes,
                        local_min_free_bytes, offbox_min_free_bytes,
                        legacy_conflict_detected, retention_review_required,
                        retention_activated_at, local_destination_generation,
                        offbox_destination_generation, local_path_fingerprint,
                        created_at, updated_at
                    ) VALUES (
                        'global', 'hourly', 'nas.local', 'backup', 'family-cfo',
                        'operator', 'ciphertext', 'WORKGROUP',
                        'tiered', 3, 14, 90, 'keep_all', NULL, NULL, NULL,
                        1000, :offbox_max_bytes, 0, 0, 0, 0, :now,
                        '10000000-0000-0000-0000-000000000001',
                        '20000000-0000-0000-0000-000000000001', NULL,
                        :now, :now
                    )
                    """
                ),
                {"now": now, "offbox_max_bytes": offbox_max_bytes},
            )
    finally:
        engine.dispose()

    downgraded = _run_alembic(
        "downgrade", "0092_uppercase_currency_codes", database_url=database_url
    )
    assert downgraded.returncode == 0, downgraded.stderr

    engine = create_engine(database_url)
    try:
        schema = inspect(engine)
        assert "backup_settings" not in schema.get_table_names()
        assert "backup_retention_events" not in schema.get_table_names()
        assert "prune_reason" not in {
            column["name"] for column in schema.get_columns("backup_jobs")
        }
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT backup_frequency, backup_smb_host, backup_smb_share,
                           backup_smb_folder, backup_smb_username,
                           backup_smb_password_encrypted, backup_smb_domain,
                           backup_max_bytes
                    FROM households
                    """
                )
            ).mappings().one()
        assert dict(row) == {
            "backup_frequency": "hourly",
            "backup_smb_host": "nas.local",
            "backup_smb_share": "backup",
            "backup_smb_folder": "family-cfo",
            "backup_smb_username": "operator",
            "backup_smb_password_encrypted": "ciphertext",
            "backup_smb_domain": "WORKGROUP",
            "backup_max_bytes": expected_legacy_max_bytes,
        }
    finally:
        engine.dispose()
