"""Aggregate, privacy-safe counters for the search pipeline.

Privacy contract: this module only ever sees *counts* and provider *names*.
There is no parameter for a query, a URL, a client address or a result, so
those values cannot reach the snapshot even by mistake. The counters live in
process memory only: nothing is written to disk, nothing is logged, and a
restart starts from zero.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable


class SearchMetrics:
    """Process-wide search counters, safe to read while requests are running."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        # The event loop updates the counters from request handlers, but the
        # snapshot is also served from a threadpool in some deployments: a lock
        # keeps a read from observing a half-written dict.
        self._lock = threading.Lock()
        self._searches_total = 0
        self._searches_failed_total = 0
        self._searches_degraded_total = 0
        self._searches_empty_total = 0
        self._local_hits_total = 0
        self._external_hits_total = 0
        self._provider_errors: dict[str, int] = {}

    def record_search(
        self,
        *,
        local_hits: int,
        external_hits: int,
        provider_errors: Iterable[str] = (),
        degraded: bool = False,
        empty: bool = False,
    ) -> None:
        """Count one answered search. Values are counts and provider names only."""

        names = tuple(dict.fromkeys(provider_errors))
        with self._lock:
            self._searches_total += 1
            self._local_hits_total += max(0, local_hits)
            self._external_hits_total += max(0, external_hits)
            if degraded:
                self._searches_degraded_total += 1
            if empty:
                self._searches_empty_total += 1
            for name in names:
                self._provider_errors[name] = self._provider_errors.get(name, 0) + 1

    def record_failure(self) -> None:
        """Count a search that no backend could answer (the API returns 503)."""

        with self._lock:
            self._searches_total += 1
            self._searches_failed_total += 1

    def snapshot(self) -> dict[str, object]:
        """Return a copy of every counter, plus the process uptime in seconds."""

        with self._lock:
            return {
                "uptime_s": max(0, int(self._clock() - self._started_at)),
                "searches_total": self._searches_total,
                "searches_failed_total": self._searches_failed_total,
                "searches_degraded_total": self._searches_degraded_total,
                "searches_empty_total": self._searches_empty_total,
                "local_hits_total": self._local_hits_total,
                "external_hits_total": self._external_hits_total,
                "provider_errors_total": dict(self._provider_errors),
            }
