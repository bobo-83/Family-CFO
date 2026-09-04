from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3


DEFAULT_RETRY_POLICY = RetryPolicy()


class RetryExhaustedError(RuntimeError):
    """Raised when a job fails on every attempt allowed by its RetryPolicy."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        super().__init__(f"job failed after {attempts} attempts: {last_error}")
        self.attempts = attempts
        self.last_error = last_error


def run_with_retry[T](
    func: Callable[[], T],
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    on_attempt_failure: Callable[[Exception, int], None] | None = None,
    should_retry: Callable[[Exception], bool] | None = None,
) -> T:
    """Call ``func`` up to ``policy.max_attempts`` times, retrying immediately on failure.

    Raises ``RetryExhaustedError`` (wrapping the last exception) once
    attempts are exhausted. ``on_attempt_failure(error, attempt_number)``
    runs after every failed attempt, including the last, so callers can
    persist progress (e.g. incrementing a retry counter) without duplicating
    retry bookkeeping themselves. When ``should_retry`` rejects an exception,
    that exception is raised unchanged and the failure callback is not run.
    """
    last_error: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return func()
        except Exception as exc:
            if should_retry is not None and not should_retry(exc):
                raise
            last_error = exc
            if on_attempt_failure is not None:
                on_attempt_failure(exc, attempt)

    assert last_error is not None
    raise RetryExhaustedError(policy.max_attempts, last_error) from last_error
