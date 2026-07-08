from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3


class RetryExhaustedError(RuntimeError):
    """Raised when a job fails on every attempt allowed by its RetryPolicy."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        super().__init__(f"job failed after {attempts} attempts: {last_error}")
        self.attempts = attempts
        self.last_error = last_error


def run_with_retry(
    func: Callable[[], T],
    policy: RetryPolicy = RetryPolicy(),
    on_attempt_failure: Callable[[Exception, int], None] | None = None,
) -> T:
    """Call ``func`` up to ``policy.max_attempts`` times, retrying immediately on failure.

    Raises ``RetryExhaustedError`` (wrapping the last exception) once
    attempts are exhausted. ``on_attempt_failure(error, attempt_number)``
    runs after every failed attempt, including the last, so callers can
    persist progress (e.g. incrementing a retry counter) without duplicating
    retry bookkeeping themselves.
    """
    last_error: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - any job failure should be retried
            last_error = exc
            if on_attempt_failure is not None:
                on_attempt_failure(exc, attempt)

    assert last_error is not None
    raise RetryExhaustedError(policy.max_attempts, last_error) from last_error
