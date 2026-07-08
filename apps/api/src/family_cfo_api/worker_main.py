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

from family_cfo_api import backup_processing, import_processing, report_generation
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
            name="run-daily-backup",
            func=run_daily_backup,
            interval_seconds=BACKUP_INTERVAL_SECONDS,
        )
    )
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
