"""Entry point for the background import-processing worker.

Not started automatically by the API process (`main.py`) — this is a
separate long-running process, matching the `family-cfo-worker` container
planned in `docs/specs/10-docker-spec.md`. Run it with:

    family-cfo-worker
"""

from __future__ import annotations

import logging
import time

from family_cfo_scheduler import Job, Scheduler

from family_cfo_api import (
    ai_memory,
    backup_processing,
    import_processing,
    net_worth_history,
    report_generation,
    vector_indexing,
)
from family_cfo_api.config import get_settings
from family_cfo_api.db import create_database_engine
from family_cfo_api.logging import configure_logging

IMPORT_POLL_INTERVAL_SECONDS = 30
REPORT_POLL_INTERVAL_SECONDS = 3600
BACKUP_INTERVAL_SECONDS = 86400


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings.database_url)
    logger = logging.getLogger(__name__)

    def process_pending_imports() -> None:
        import_processing.run_pending_imports_once(engine, settings.import_staging_dir)

    def generate_weekly_reports() -> None:
        report_generation.run_scheduled_reports_once(engine, "weekly")

    def generate_monthly_reports() -> None:
        report_generation.run_scheduled_reports_once(engine, "monthly")

    def generate_annual_reports() -> None:
        report_generation.run_scheduled_reports_once(engine, "annual")

    def sync_bank_connections() -> None:
        # M27: pull statements from every linked institution, deduped (ADR 0015).
        from family_cfo_api import banksync, repository

        for connection in repository.list_all_institution_connections(engine):
            try:
                banksync.sync_connection(engine, settings, connection)
            except banksync.BankSyncError:
                # Error already recorded on the connection; keep syncing others.
                continue

    def capture_net_worth_snapshot() -> None:
        # M40: one snapshot per household per day for the Overview trend.
        net_worth_history.record_snapshot_once(engine)

    def rebuild_vector_index() -> None:
        # M69: daily wipe-and-rebuild prunes vectors of deleted rows.
        vector_indexing.run_indexing_once(engine, settings, wipe=True)

    def run_daily_backup() -> None:
        backup_processing.run_backup_once(
            engine,
            database_url=settings.database_url,
            staging_dir=settings.import_staging_dir,
            backup_dir=settings.backup_dir,
            encryption_key=settings.backup_encryption_key,
            retention_count=settings.backup_retention_count,
        )

    scheduler = Scheduler()
    scheduler.add_job(
        Job(
            name="process-pending-imports",
            func=process_pending_imports,
            interval_seconds=IMPORT_POLL_INTERVAL_SECONDS,
        )
    )
    scheduler.add_job(
        Job(
            name="generate-weekly-reports",
            func=generate_weekly_reports,
            interval_seconds=REPORT_POLL_INTERVAL_SECONDS,
        )
    )
    scheduler.add_job(
        Job(
            name="generate-monthly-reports",
            func=generate_monthly_reports,
            interval_seconds=REPORT_POLL_INTERVAL_SECONDS,
        )
    )
    scheduler.add_job(
        Job(
            name="generate-annual-reports",
            func=generate_annual_reports,
            interval_seconds=REPORT_POLL_INTERVAL_SECONDS,
        )
    )
    scheduler.add_job(
        Job(
            name="run-daily-backup",
            func=run_daily_backup,
            interval_seconds=BACKUP_INTERVAL_SECONDS,
        )
    )
    scheduler.add_job(
        Job(
            name="sync-bank-connections",
            func=sync_bank_connections,
            interval_seconds=BACKUP_INTERVAL_SECONDS,  # daily, same cadence as backups
        )
    )
    scheduler.add_job(
        Job(
            name="capture-net-worth-snapshot",
            func=capture_net_worth_snapshot,
            interval_seconds=BACKUP_INTERVAL_SECONDS,  # daily
        )
    )
    scheduler.add_job(
        Job(
            name="rebuild-vector-index",
            func=rebuild_vector_index,
            interval_seconds=BACKUP_INTERVAL_SECONDS,  # daily
        )
    )

    # M40: capture one snapshot immediately so the trend has a starting point
    # rather than waiting a full day for the first interval to fire.
    try:
        capture_net_worth_snapshot()
    except Exception:  # noqa: BLE001 - a snapshot failure must not stop the worker
        logger.exception("initial net-worth snapshot failed")

    # M57 (ADR 0016): one-time memory extraction from conversations that
    # predate the feature. Households already marked done are skipped;
    # households without a usable runtime stay unmarked and retry next start.
    try:
        backfilled = ai_memory.run_memory_backfill_once(engine, settings)
        if backfilled:
            logger.info("memory backfill completed for %s household(s)", backfilled)
    except Exception:  # noqa: BLE001 - a backfill failure must not stop the worker
        logger.exception("memory backfill failed")

    # M69: index existing records at startup (additive; the daily job prunes).
    vector_indexing.run_indexing_once(engine, settings)

    scheduler.start()

    logger.info(
        "worker started: imports every %ss, reports every %ss, backup every %ss",
        IMPORT_POLL_INTERVAL_SECONDS,
        REPORT_POLL_INTERVAL_SECONDS,
        BACKUP_INTERVAL_SECONDS,
    )

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
