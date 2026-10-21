"""Per-provider circuit breaker.

A provider that keeps failing should not cost the full timeout on every
search. After ``failure_threshold`` consecutive errors the provider is skipped
for ``cooldown_s``; the next call after the cooldown is a trial that either
closes the circuit again (success) or re-opens it for another cooldown.

Only provider names and counters are kept: no query, no URL, no IP.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class CircuitBreaker:
    """Consecutive-failure breaker with one state per provider name."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if cooldown_s <= 0:
            raise ValueError("cooldown_s must be positive")

        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def available(self, provider: str) -> bool:
        """Whether a call may be attempted right now."""

        opened_until = self._open_until.get(provider)
        if opened_until is None:
            return True
        return self._clock() >= opened_until

    def record_success(self, provider: str) -> None:
        """A provider answered: close its circuit."""

        self._failures.pop(provider, None)
        self._open_until.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        """A provider failed: count it and open the circuit at the threshold."""

        failures = self._failures.get(provider, 0) + 1
        self._failures[provider] = failures
        if failures >= self._failure_threshold:
            self._open_until[provider] = self._clock() + self._cooldown_s

    def failures(self, provider: str) -> int:
        """Consecutive failures currently counted for a provider."""

        return self._failures.get(provider, 0)

    def is_open(self, provider: str) -> bool:
        """True while the provider is inside a cooldown window."""

        return not self.available(provider)
