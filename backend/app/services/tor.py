"""Tor exit-node diagnostics.

The checker asks ``check.torproject.org`` *through* the configured SOCKS5
proxy, so the answer describes the actual Tor circuit used by this server, not
the user. It is isolated from the search path: a stopped Tor proxy only makes
this endpoint wait up to ``TOR_TIMEOUT_S``, never a search request.

Privacy: only the exit-node address is returned (it identifies the server's
circuit, not the user). Results are cached in memory, never persisted, and the
logs contain reason codes only.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

MAX_EXIT_IP_LENGTH = 64


@dataclass(frozen=True, slots=True)
class TorCheck:
    """Outcome of one probe: never raises, always reports a reason code."""

    active: bool
    exit_ip: str | None = None
    latency_ms: int | None = None
    error: str | None = None


Probe = Callable[[], Awaitable[TorCheck]]
ClientFactory = Callable[[], httpx.AsyncClient]


class TorStatusService:
    """Probes the Tor proxy and caches the result briefly.

    The cache is intentionally short: the frontend polls, and every uncached
    check opens a new circuit through check.torproject.org.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        probe: Probe | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._settings = settings
        self._make_client = client_factory or self._build_client
        self._probe = probe or self._probe_http
        self._lock = asyncio.Lock()
        self._cached: TorCheck | None = None
        self._cached_at = 0.0

    async def check(self) -> TorCheck:
        """Return the cached verdict when fresh, otherwise probe once."""

        if not self._settings.tor_enabled:
            return TorCheck(active=False, error="tor_disabled")

        ttl = self._settings.tor_cache_ttl_s
        cached = self._fresh(ttl)
        if cached is not None:
            return cached

        async with self._lock:
            # Another request may have refreshed while this one was waiting.
            cached = self._fresh(ttl)
            if cached is not None:
                return cached

            result = await self._probe()
            self._cached = result
            self._cached_at = time.monotonic()
            if not result.active:
                logger.info("Tor check failed: %s", result.error)
            return result

    def _fresh(self, ttl: float) -> TorCheck | None:
        if ttl <= 0 or self._cached is None:
            return None
        if time.monotonic() - self._cached_at >= ttl:
            return None
        return self._cached

    def _build_client(self) -> httpx.AsyncClient:
        # trust_env=False: an HTTP_PROXY/ALL_PROXY variable must not silently
        # bypass the Tor SOCKS5 endpoint we are testing.
        return httpx.AsyncClient(
            proxy=self._settings.tor_proxy_url,
            timeout=httpx.Timeout(self._settings.tor_timeout_s),
            follow_redirects=False,
            trust_env=False,
        )

    async def _probe_http(self) -> TorCheck:
        started = time.perf_counter()
        try:
            async with self._make_client() as client:
                response = await client.get(
                    self._settings.tor_status_url,
                    headers={"Accept": "application/json"},
                )
        except httpx.TimeoutException:
            return TorCheck(active=False, error="tor_timeout")
        except httpx.HTTPError as exc:
            logger.warning("Tor proxy unreachable (%s)", type(exc).__name__)
            return TorCheck(active=False, error="tor_unreachable")
        except Exception as exc:  # noqa: BLE001 - a missing socksio must not 500 the endpoint
            # E.g. ImportError when httpx[socks]/socksio is not installed.
            logger.warning("Tor check failed (%s)", type(exc).__name__)
            return TorCheck(active=False, error="tor_unreachable")

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            return TorCheck(active=False, latency_ms=latency_ms, error=f"tor_http_{response.status_code}")

        try:
            payload = response.json()
        except ValueError:
            return TorCheck(active=False, latency_ms=latency_ms, error="tor_invalid_json")
        if not isinstance(payload, dict):
            return TorCheck(active=False, latency_ms=latency_ms, error="tor_invalid_payload")

        exit_ip = self._clean_ip(payload.get("IP"))
        if payload.get("IsTor") is True:
            return TorCheck(active=True, exit_ip=exit_ip, latency_ms=latency_ms)
        return TorCheck(
            active=False,
            exit_ip=exit_ip,
            latency_ms=latency_ms,
            error="tor_not_an_exit",
        )

    @staticmethod
    def _clean_ip(value: object) -> str | None:
        """Accept only a syntactically valid address: the field reaches the UI."""

        if not isinstance(value, str):
            return None
        candidate = value.strip()
        if not candidate or len(candidate) > MAX_EXIT_IP_LENGTH:
            return None
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            return None
        return candidate
