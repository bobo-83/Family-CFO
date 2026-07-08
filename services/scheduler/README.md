# Scheduler

The scheduler runs background tasks.

Responsibilities:

- Weekly reports
- Monthly reports
- Annual summaries
- Statement import jobs
- Backup jobs
- Model health checks

## M7 Scope

Implemented as the `family_cfo_scheduler` package: generic scheduling and retry primitives, with
no knowledge of what a job actually does. The M7 import-processing job itself lives in
`apps/api/src/family_cfo_api/import_processing.py`, which depends on this package rather than the
other way around — `family_cfo_scheduler` has no database dependency, matching the other
`services/*` packages (financial-engine, ai-orchestrator, ocr-worker).

- `RetryPolicy` / `run_with_retry`: calls a zero-argument function up to `max_attempts` times, raising `RetryExhaustedError` (wrapping the last exception) once exhausted. `on_attempt_failure(error, attempt_number)` runs after every failed attempt so callers can persist progress (e.g. an import's `retry_count`) without duplicating retry bookkeeping.
- `Job` / `Scheduler`: `Job` is a plain `(name, func, interval_seconds)` tuple-like dataclass. `Scheduler` wraps APScheduler's `BackgroundScheduler` with an `IntervalTrigger` for real deployments — no message broker, jobs run in-process against the same database (ADR 0006).
- `run_job_once(job)`: calls `job.func()` directly, bypassing the scheduler entirely. Tests call this (or the job's underlying function directly) rather than starting a real scheduler thread, keeping job behavior deterministic and fast to verify.

## Running the Worker

`apps/api` provides the actual entry point (`family-cfo-worker`, see `apps/api/README.md`) that
wires this package's `Scheduler` to the import-processing job. It is a separate long-running
process from the API server — not started automatically — matching the `family-cfo-worker`
container planned in `docs/specs/10-docker-spec.md`.

## Tests

```bash
cd services/scheduler
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
```
