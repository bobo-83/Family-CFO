import threading

from family_cfo_scheduler.worker import Job, Scheduler, run_job_once


def test_run_job_once_calls_the_function_directly() -> None:
    calls = []
    job = Job(name="test-job", func=lambda: calls.append(1), interval_seconds=60)

    run_job_once(job)

    assert calls == [1]


def test_scheduler_runs_a_job_on_its_interval() -> None:
    ran = threading.Event()
    job = Job(name="fast-job", func=ran.set, interval_seconds=1)

    scheduler = Scheduler()
    scheduler.add_job(job)
    scheduler.start()
    try:
        assert ran.wait(timeout=5)
    finally:
        scheduler.shutdown(wait=False)


def test_scheduler_replaces_existing_job_with_same_name() -> None:
    scheduler = Scheduler()
    scheduler.add_job(Job(name="dup", func=lambda: None, interval_seconds=60))
    # Should not raise even though a job with this name already exists.
    scheduler.add_job(Job(name="dup", func=lambda: None, interval_seconds=60))
