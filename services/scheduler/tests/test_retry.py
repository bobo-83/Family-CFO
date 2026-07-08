import pytest

from family_cfo_scheduler.retry import RetryExhaustedError, RetryPolicy, run_with_retry


def test_succeeds_on_first_attempt() -> None:
    calls = []

    def job() -> str:
        calls.append(1)
        return "ok"

    result = run_with_retry(job)

    assert result == "ok"
    assert len(calls) == 1


def test_succeeds_after_transient_failures() -> None:
    attempts = {"count": 0}

    def job() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient")
        return "ok"

    result = run_with_retry(job, RetryPolicy(max_attempts=3))

    assert result == "ok"
    assert attempts["count"] == 3


def test_raises_retry_exhausted_after_max_attempts() -> None:
    def job() -> None:
        raise RuntimeError("permanent failure")

    with pytest.raises(RetryExhaustedError) as exc_info:
        run_with_retry(job, RetryPolicy(max_attempts=3))

    assert exc_info.value.attempts == 3
    assert isinstance(exc_info.value.last_error, RuntimeError)


def test_on_attempt_failure_called_for_every_failed_attempt() -> None:
    failures: list[int] = []

    def job() -> None:
        raise RuntimeError("always fails")

    def on_failure(_error: Exception, attempt: int) -> None:
        failures.append(attempt)

    with pytest.raises(RetryExhaustedError):
        run_with_retry(job, RetryPolicy(max_attempts=3), on_attempt_failure=on_failure)

    assert failures == [1, 2, 3]
