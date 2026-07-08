from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    func: Callable[[], None]
    interval_seconds: int


def run_job_once(job: Job) -> None:
    """Invoke a job's function directly, bypassing the scheduler.

    Tests call this rather than starting a real scheduler thread, keeping
    job behavior deterministic and fast to verify.
    """
    job.func()


class Scheduler:
    """A thin wrapper around APScheduler's ``BackgroundScheduler`` for real deployments.

    No message broker — jobs run in-process against the same database,
    consistent with keeping the self-hosted deployment simple (ADR 0006).
    """

    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler()

    def add_job(self, job: Job) -> None:
        self._scheduler.add_job(
            job.func,
            trigger=IntervalTrigger(seconds=job.interval_seconds),
            id=job.name,
            replace_existing=True,
        )

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self, wait: bool = True) -> None:
        self._scheduler.shutdown(wait=wait)
