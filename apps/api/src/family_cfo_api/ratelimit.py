"""In-memory brute-force limiter for authentication (ADR 0010).

Best-effort, single-instance: state lives in the process, so counters reset on
restart and do not span replicas. That is an accepted tradeoff for the
single-`api`-container home-server model; operators exposing the stack should
still front it with an authenticating proxy (ADR 0008). This is defense in
depth, not the only line.
"""

from __future__ import annotations

import threading
import time


class AuthRateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int,
        window_seconds: int,
        lockout_seconds: int,
        enabled: bool = True,
    ) -> None:
        self._max_attempts = max_attempts
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._enabled = enabled
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, keys: list[str], *, now: float | None = None) -> int | None:
        """Seconds the caller must wait if any key is locked out, else None."""
        if not self._enabled:
            return None
        now = now if now is not None else time.monotonic()
        with self._lock:
            wait = 0.0
            for key in keys:
                until = self._locked_until.get(key)
                if until is not None and until > now:
                    wait = max(wait, until - now)
            return int(wait) + 1 if wait > 0 else None

    def record_failure(self, keys: list[str], *, now: float | None = None) -> None:
        if not self._enabled:
            return
        now = now if now is not None else time.monotonic()
        with self._lock:
            for key in keys:
                recent = [t for t in self._failures.get(key, []) if t > now - self._window]
                recent.append(now)
                self._failures[key] = recent
                if len(recent) >= self._max_attempts:
                    self._locked_until[key] = now + self._lockout
                    self._failures[key] = []

    def reset(self, keys: list[str]) -> None:
        if not self._enabled:
            return
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
                self._locked_until.pop(key, None)
