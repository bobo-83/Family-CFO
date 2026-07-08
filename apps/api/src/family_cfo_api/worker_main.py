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

from family_cfo_api import import_processing
from family_cfo_api.config import get_settings
from family_cfo_api.db import create_database_engine
from family_cfo_api.logging import configure_logging

IMPORT_POLL_INTERVAL_SECONDS = 30


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings.database_url)

    def process_pending_imports() -> None:
        import_processing.run_pending_imports_once(engine, settings.import_staging_dir)

    scheduler = Scheduler()
    scheduler.add_job(
        Job(
            name="process-pending-imports",
            func=process_pending_imports,
            interval_seconds=IMPORT_POLL_INTERVAL_SECONDS,
        )
    )
    scheduler.start()

    logging.getLogger(__name__).info(
        "worker started, polling pending imports every %s seconds", IMPORT_POLL_INTERVAL_SECONDS
    )

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
