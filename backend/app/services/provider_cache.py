"""Short-lived, in-memory cache for external provider responses.

The cache exists to avoid paying for the same provider quota twice when a
query is repeated within a few minutes (a reload, a shared link, an explicit
retry). It is deliberately minimal:

* it lives only in process memory: no disk, no database, no log line;
* the key is ``(provider, normalised query, page, size)``, so the query is
  never stored anywhere outside the process;
* entries expire after ``ttl_s`` and the mapping is bounded, so a long-running
  process cannot grow without limit.

Consecutive identical searches therefore consume one provider call each time
the TTL allows it, not one per request.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.services.results import BackendResult

CacheKey = tuple[str, str, int, int]


def cache_key(
    provider: str,
    query: str,
    *,
    page: int,
    size: int,
) -> CacheKey:
    """Normalise a provider request into a cache key.

    Whitespace is collapsed and case is folded: ``"  Auto   usata "`` and
    ``"auto usata"`` are the same search for a provider. The page and the page
    size are part of the key because they produce different result windows.
    """

    normalised = " ".join(query.casefold().split())
    return (provider, normalised, max(1, page), max(1, size))


class ProviderCache:
    """Bounded mapping from a provider request to its response."""

    def __init__(
        self,
        *,
        ttl_s: float = 180.0,
        max_entries: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be positive")

        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock
        # Insertion order doubles as the eviction order: dicts preserve it.
        self._entries: dict[CacheKey, tuple[float, BackendResult]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: CacheKey) -> BackendResult | None:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None

        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            self.misses += 1
            return None

        self.hits += 1
        return value

    def put(self, key: CacheKey, value: BackendResult) -> None:
        now = self._clock()
        self._entries.pop(key, None)

        if len(self._entries) >= self._max_entries:
            self._prune(now)
            while len(self._entries) >= self._max_entries:
                oldest = next(iter(self._entries))
                del self._entries[oldest]

        self._entries[key] = (now + self._ttl_s, value)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def _prune(self, now: float) -> None:
        expired = [
            key for key, (expires_at, _) in self._entries.items() if now >= expires_at
        ]
        for key in expired:
            del self._entries[key]
