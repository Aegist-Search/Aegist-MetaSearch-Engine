"""Small in-memory rate limiters for background jobs and HTTP clients.

The on-demand crawler is triggered by a user search, so an exposed instance
must not be able to start an unbounded number of Katana processes. A token
bucket gives an average rate (starts per minute) while still allowing a short
burst, which is what a real search session looks like.

Privacy: the crawl bucket is a single counter. No IP, no query and no domain is
kept, so it cannot be used to profile callers. The HTTP limiter below keeps one
bucket per salted hash of the client address (never the address itself) and
logs nothing; behind the Next.js server every request shares the same address,
so there the effective bucket is global.
"""

from __future__ import annotations

import hashlib
import math
import secrets
import time
from collections.abc import Callable


class TokenBucket:
    """Classic token bucket with a monotonic clock.

    ``capacity`` is the maximum burst, ``refill_per_minute`` the sustained rate.
    A rejected call does not consume anything.
    """

    __slots__ = ("_capacity", "_rate_per_s", "_tokens", "_updated", "_clock")

    def __init__(
        self,
        capacity: int,
        refill_per_minute: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        if refill_per_minute <= 0:
            raise ValueError("refill_per_minute must be positive")

        self._capacity = float(capacity)
        self._rate_per_s = refill_per_minute / 60.0
        self._tokens = float(capacity)
        self._clock = clock
        self._updated = clock()

    def take(self, amount: float = 1.0) -> bool:
        """Consume ``amount`` tokens when available, otherwise return ``False``."""

        if amount <= 0:
            raise ValueError("amount must be positive")

        now = self._clock()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_s)
            self._updated = now

        if self._tokens < amount:
            return False
        self._tokens -= amount
        return True

    def seconds_until(self, amount: float = 1.0) -> float:
        """Seconds until ``amount`` tokens are available, without consuming any."""

        if amount <= 0:
            raise ValueError("amount must be positive")
        missing = amount - self.available
        if missing <= 0:
            return 0.0
        return missing / self._rate_per_s

    @property
    def available(self) -> float:
        """Tokens available right now, including the refill since the last take.

        Read-only on purpose: health reporting and tests must not consume or
        mutate the bucket. The next ``take`` recomputes the same value and
        advances the timestamp.
        """

        now = self._clock()
        elapsed = now - self._updated
        if elapsed <= 0:
            return self._tokens
        return min(self._capacity, self._tokens + elapsed * self._rate_per_s)


class ClientRateLimiter:
    """Per-client token buckets for the public HTTP endpoints.

    The client address is never kept: it is hashed with a random salt that
    lives only in this process (``key_for``), and the digest is the only key in
    the mapping. Buckets are bounded, so a long-running process cannot grow.

    Behind the Next.js Server Components every backend request comes from the
    Next.js process, so the effective bucket is global; the limiter still
    protects provider quotas and the crawl from a runaway caller. Raw client
    addresses are never logged, stored or returned.
    """

    def __init__(
        self,
        *,
        burst: int = 20,
        refill_per_minute: float = 60.0,
        max_clients: int = 4096,
        clock: Callable[[], float] = time.monotonic,
        salt: bytes | None = None,
    ) -> None:
        if burst < 1:
            raise ValueError("burst must be at least 1")
        if refill_per_minute <= 0:
            raise ValueError("refill_per_minute must be positive")
        if max_clients < 1:
            raise ValueError("max_clients must be at least 1")

        self._burst = burst
        self._refill_per_minute = refill_per_minute
        self._max_clients = max_clients
        self._clock = clock
        self._salt = salt or secrets.token_bytes(16)
        self._buckets: dict[str, TokenBucket] = {}

    def key_for(self, identifier: str) -> str:
        """Salted digest used as the bucket key (never the address itself)."""

        return hashlib.blake2b(
            identifier.encode("utf-8"),
            key=self._salt,
            digest_size=16,
        ).hexdigest()

    def hit(self, identifier: str) -> tuple[bool, int]:
        """Consume one token and return ``(allowed, retry_after_seconds)``."""

        key = self.key_for(identifier)
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self._max_clients:
                # FIFO eviction: the oldest bucket is the least recently created
                # one. Refilled buckets are cheap to recreate, so no timestamp
                # or client address is kept just for eviction.
                del self._buckets[next(iter(self._buckets))]
            bucket = TokenBucket(
                capacity=self._burst,
                refill_per_minute=self._refill_per_minute,
                clock=self._clock,
            )
            self._buckets[key] = bucket

        if bucket.take():
            return True, 0

        return False, max(1, math.ceil(bucket.seconds_until()))

    @property
    def clients(self) -> int:
        """Number of buckets currently held in memory."""

        return len(self._buckets)
