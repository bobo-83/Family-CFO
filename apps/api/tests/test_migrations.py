from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.config import Settings

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


def _postgresql_required() -> bool:
    return os.getenv("FAMILY_CFO_REQUIRE_POSTGRESQL", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _postgresql_test_engine() -> Engine:
    required = _postgresql_required()
    database_url = os.getenv("FAMILY_CFO_TEST_DATABASE_URL", "").strip()
    if not database_url:
        if required:
            pytest.fail(
                "FAMILY_CFO_REQUIRE_POSTGRESQL is set but "
                "FAMILY_CFO_TEST_DATABASE_URL is not configured"
            )
        pytest.skip("FAMILY_CFO_TEST_DATABASE_URL is not configured")

    try:
        engine = create_engine(database_url)
    except Exception as exc:  # noqa: BLE001 - required mode turns environment failure loud
        if required:
            pytest.fail(f"required PostgreSQL test driver is unavailable: {type(exc).__name__}")
        pytest.skip(f"PostgreSQL test driver is unavailable: {type(exc).__name__}")

    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.fail("FAMILY_CFO_TEST_DATABASE_URL must identify PostgreSQL")
    try:
        with engine.connect() as connection:
            version_num = int(connection.execute(text("SHOW server_version_num")).scalar_one())
    except Exception as exc:  # noqa: BLE001 - required mode turns environment failure loud
        engine.dispose()
        if required:
            pytest.fail(f"required PostgreSQL test database is unavailable: {type(exc).__name__}")
        pytest.skip(f"PostgreSQL test database is unavailable: {type(exc).__name__}")
    if version_num // 10_000 != 17:
        engine.dispose()
        pytest.fail("FAMILY_CFO_TEST_DATABASE_URL must identify PostgreSQL 17")
    return engine


def test_migrations_upgrade_and_downgrade_cleanly(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration_rollback_test.db'}"

    upgraded = _run_alembic("upgrade", "head", database_url=database_url)
    assert upgraded.returncode == 0, upgraded.stderr

    downgraded = _run_alembic("downgrade", "base", database_url=database_url)
    assert downgraded.returncode == 0, downgraded.stderr

    re_upgraded = _run_alembic("upgrade", "head", database_url=database_url)
    assert re_upgraded.returncode == 0, re_upgraded.stderr


def test_postgresql_17_backup_migrations_and_singleton_bootstrap() -> None:
    admin_engine = _postgresql_test_engine()
    schema_name = f"family_cfo_migration_{uuid4().hex}"
    schema_url = admin_engine.url.update_query_dict({"options": f"-csearch_path={schema_name}"})
    database_url = schema_url.render_as_string(hide_password=False)
    schema_created = False
    try:
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            schema_created = True
        except Exception as exc:  # noqa: BLE001 - required mode turns environment failure loud
            if _postgresql_required():
                pytest.fail(f"required PostgreSQL test schema is unavailable: {type(exc).__name__}")
            pytest.skip(f"PostgreSQL test schema is unavailable: {type(exc).__name__}")

        pre_backup = _run_alembic(
            "upgrade", "0092_uppercase_currency_codes", database_url=database_url
        )
        assert pre_backup.returncode == 0, pre_backup.stderr

        schema_engine = create_engine(database_url)
        try:
            now = "2026-09-10 12:00:00+00:00"
            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO households (
                            id, display_name, base_currency, backup_frequency,
                            backup_max_bytes, created_at, updated_at
                        ) VALUES (
                            '00000000-0000-0000-0000-000000000017',
                            'Synthetic migration household', 'USD', 'hourly',
                            123456, :now, :now
                        )
                        """
                    ),
                    {"now": now},
                )
        finally:
            schema_engine.dispose()

        upgraded = _run_alembic("upgrade", "head", database_url=database_url)
        assert upgraded.returncode == 0, upgraded.stderr

        schema_engine = create_engine(database_url)
        try:
            schema = inspect(schema_engine)
            assert "backup_settings" in schema.get_table_names()
            assert "backup_retention_events" in schema.get_table_names()
            assert {column["name"] for column in schema.get_columns("backup_jobs")} >= {
                "prune_reason"
            }
            with schema_engine.connect() as connection:
                assert connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one() == ("0094_backup_delete_intents")
                assert (
                    connection.execute(text("SELECT count(*) FROM backup_settings")).scalar_one()
                    == 0
                )

            settings = Settings(
                database_url=database_url,
                backup_dir="/synthetic/backups",
                backup_retention_count=9,
                offbox_backup_retention_days=30,
            )
            stored = repository.get_backup_settings(
                schema_engine,
                settings=settings,
                as_of=datetime(2026, 9, 10, 12, tzinfo=UTC),
            )
            assert stored.key == "global"
            assert stored.frequency == "hourly"
            assert stored.local_max_bytes == 123456
            assert stored.offbox_max_bytes == 123456
            assert stored.local_retention.keep_all_days == 3
            assert stored.local_retention.daily_until_days == 14
            assert stored.local_retention.weekly_until_days == 90
            assert stored.offbox_retention.keep_all_days == 30
            assert stored.offbox_retention.daily_until_days == 30
            assert stored.offbox_retention.weekly_until_days == 30
            assert stored.retention_review_required is True
            assert stored.retention_activated_at is None

            with schema_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO backup_retention_events (
                            id, destination, operation_id, event_key,
                            destination_generation, action, reason, occurred_at
                        ) VALUES (
                            '00000000-0000-0000-0000-000000000094', 'local',
                            'migration-test', 'migration-test-delete-pending',
                            :generation, 'delete_pending', 'test', :occurred_at
                        )
                        """
                    ),
                    {
                        "generation": stored.local_destination_generation,
                        "occurred_at": stored.updated_at,
                    },
                )
        finally:
            schema_engine.dispose()
    finally:
        if schema_created:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


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
