"""Dynamic SOCKS5 proxy rotation for outbound provider calls.

The module has two responsibilities:

* :class:`ProxyManager` downloads a public SOCKS5 list, validates the entries,
  keeps a health-aware round-robin pool and owns one reusable ``httpx`` client
  per proxy (a client per proxy is required because ``httpx`` binds the proxy
  to the transport at construction time).
* :class:`ProxiedHttpClient` is the thin facade the provider clients use. It
  sends a request through the next healthy proxy, evicts a failing one and
  retries immediately with another candidate. When the pool is exhausted it
  optionally falls back to the server's direct connection.

Privacy rules enforced here:

* no query string, destination URL, API key, proxy address or user IP is ever
  logged; only reason codes and aggregate counters are emitted;
* a proxy is only ever used for HTTPS provider endpoints, so the tunnel sees
  the destination hostname but not the request contents;
* the local index (Meilisearch), the preview downloader and the proxy-list
  download itself deliberately stay on the direct connection.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import quote

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

#: SOCKS5 greeting: version 5, one offered method, "no authentication".
_SOCKS5_GREETING = b"\x05\x01\x00"
#: SOCKS5 method-selection reply: version 5 + "no auth" or "user/pass".
_SOCKS5_METHODS = (b"\x00", b"\x02")
_MAX_PORT = 65535


@dataclass(frozen=True, slots=True)
class ProxyEndpoint:
    """A single SOCKS5 endpoint, optionally with credentials.

    ``repr`` is deliberately opaque: endpoints end up in exception tracebacks
    and log records, and a free proxy address is still infrastructure data.
    """

    host: str
    port: int
    username: str | None = None
    password: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - defensive, trivial
        return "<socks5-proxy>"

    @property
    def requires_auth(self) -> bool:
        return bool(self.username)

    @property
    def url(self) -> str:
        """Return the ``socks5://`` URL understood by httpx/socksio."""

        credentials = ""
        if self.username:
            user = quote(self.username, safe="")
            password = quote(self.password or "", safe="")
            credentials = f"{user}:{password}@"

        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"socks5://{credentials}{host}:{self.port}"


def _parse_endpoint(value: str) -> ProxyEndpoint | None:
    """Parse ``host:port``, ``user:pass@host:port`` or a bracketed IPv6 host."""

    value = value.strip()
    if not value:
        return None

    username: str | None = None
    password: str | None = None
    if "@" in value:
        credentials, value = value.rsplit("@", 1)
        if ":" in credentials:
            username, password = credentials.split(":", 1)
        else:
            username, password = credentials, ""
        if not username:
            return None
        value = value.strip()

    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        host = value[1:end].strip()
        remainder = value[end + 1 :]
        if not remainder.startswith(":"):
            return None
        port_text = remainder[1:]
    else:
        if value.count(":") != 1:
            # Unbracketed IPv6 addresses are ambiguous: reject instead of
            # guessing where the port starts.
            return None
        host, port_text = value.rsplit(":", 1)
        host = host.strip()

    if not host or any(character.isspace() for character in host):
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if not 1 <= port <= _MAX_PORT:
        return None

    return ProxyEndpoint(
        host=host,
        port=port,
        username=username or None,
        password=password,
    )


def parse_proxy_lines(raw: str, *, limit: int) -> list[ProxyEndpoint]:
    """Parse a proxy text file, de-duplicating entries and capping the result.

    Accepted forms (with or without the ``socks5://``/``socks5h://`` prefix)::

        host:port
        user:pass@host:port
        socks5://user:pass@host:port
    """

    endpoints: list[ProxyEndpoint] = []
    seen: set[ProxyEndpoint] = set()

    for line in raw.splitlines():
        candidate = line.split("#", 1)[0].strip()
        if not candidate:
            continue

        lowered = candidate.lower()
        if lowered.startswith("socks5://"):
            candidate = candidate[len("socks5://") :]
        elif lowered.startswith("socks5h://"):
            candidate = candidate[len("socks5h://") :]
        elif "://" in candidate:
            # http(s) and other proxy types are out of scope.
            continue

        endpoint = _parse_endpoint(candidate)
        if endpoint is None or endpoint in seen:
            continue

        seen.add(endpoint)
        endpoints.append(endpoint)
        if len(endpoints) >= limit:
            break

    return endpoints


class ProxyManager:
    """Health-aware SOCKS5 pool plus the per-proxy httpx client cache."""

    def __init__(self, settings: Settings, *, http: httpx.AsyncClient) -> None:
        self._settings = settings
        # Direct client used only to download the list. The application owns
        # it: the manager never closes it.
        self._http = http

        self._proxies: list[ProxyEndpoint] = []
        self._failures: dict[ProxyEndpoint, int] = {}
        self._cooldown_until: dict[ProxyEndpoint, float] = {}
        self._cursor = 0

        self._clients: dict[ProxyEndpoint, httpx.AsyncClient] = {}
        self._pool_lock = asyncio.Lock()

        self._task: asyncio.Task[None] | None = None
        self._last_refresh: float | None = None
        self._last_error: str | None = None
        self._socks_missing_warned = False

    # --- lifecycle ---------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._settings.proxy_enabled

    async def start(self) -> None:
        """Warm the pool at startup without ever blocking the app forever."""

        if not self.enabled or self._task is not None:
            return

        try:
            await asyncio.wait_for(
                self.refresh(),
                timeout=self._settings.proxy_startup_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "SOCKS5 proxy warm-up budget exceeded (reason=proxy_startup_timeout); "
                "refresh continues in background"
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - startup must never fail on proxies
            logger.warning("SOCKS5 proxy warm-up failed (reason=proxy_startup_error)")

        self._task = asyncio.create_task(self._refresh_loop(), name="socks5-proxy-refresh")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.aclose()

    async def aclose(self) -> None:
        async with self._pool_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            await client.aclose()

    async def _refresh_loop(self) -> None:
        # A cancelled or failed warm-up leaves the pool empty: retry right
        # away instead of waiting a full interval with direct-only traffic.
        if not self._proxies:
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a refresh is best effort
                logger.warning("SOCKS5 proxy refresh failed (reason=proxy_refresh_error)")

        while True:
            delay = (
                self._settings.proxy_retry_interval_s
                if not self._proxies
                else self._settings.proxy_refresh_interval_s
            )
            await asyncio.sleep(delay)
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a refresh is best effort
                logger.warning("SOCKS5 proxy refresh failed (reason=proxy_refresh_error)")

    # --- list loading ------------------------------------------------------

    async def refresh(self) -> None:
        """Download, parse and validate the list, replacing the pool atomically.

        On failure the previous pool is kept: a stale-but-working list is more
        useful than an empty one. The method never raises for network errors.
        """

        reason: str | None = None
        parts: list[str] = []

        try:
            response = await self._http.get(
                self._settings.proxy_list_url,
                headers={"Accept": "text/plain", "Cache-Control": "no-cache"},
                timeout=self._settings.proxy_fetch_timeout_s,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            reason = f"proxy_list_{type(exc).__name__.lower()}"
        else:
            if response.status_code == 200:
                parts.append(response.text)
            else:
                reason = f"proxy_list_http_{response.status_code}"

        parts.extend(self._settings.proxy_extra_list)
        candidates = parse_proxy_lines(
            "\n".join(parts),
            limit=self._settings.proxy_pool_size,
        )

        if not candidates:
            self._last_error = reason or "proxy_list_empty"
            logger.warning(
                "SOCKS5 proxy list unavailable (reason=%s); keeping %d cached proxies",
                self._last_error,
                len(self._proxies),
            )
            return

        if self._settings.proxy_validate_on_load:
            candidates = await self._validate_many(candidates)

        if not candidates:
            self._last_error = "proxy_validation_empty"
            logger.warning(
                "No SOCKS5 proxy passed validation (reason=proxy_validation_empty); "
                "keeping %d cached proxies",
                len(self._proxies),
            )
            return

        known = set(candidates)
        self._proxies = candidates
        self._failures = {
            endpoint: failures
            for endpoint, failures in self._failures.items()
            if endpoint in known
        }
        self._cooldown_until = {
            endpoint: until
            for endpoint, until in self._cooldown_until.items()
            if endpoint in known
        }
        if self._cursor >= len(self._proxies):
            self._cursor = 0

        self._last_refresh = time.monotonic()
        self._last_error = None
        logger.info("SOCKS5 proxy pool refreshed (size=%d)", len(self._proxies))

    async def _validate_many(
        self,
        candidates: Iterable[ProxyEndpoint],
    ) -> list[ProxyEndpoint]:
        semaphore = asyncio.Semaphore(self._settings.proxy_validation_concurrency)

        async def check(endpoint: ProxyEndpoint) -> ProxyEndpoint | None:
            async with semaphore:
                # Manually supplied credentials are trusted: a no-auth probe
                # would reject them by design.
                if endpoint.requires_auth:
                    return endpoint
                if self._settings.proxy_validate_url:
                    return endpoint if await self._probe_http(endpoint) else None
                return endpoint if await self._probe_socks5(endpoint) else None

        results = await asyncio.gather(
            *(check(endpoint) for endpoint in candidates),
            return_exceptions=True,
        )
        validated: list[ProxyEndpoint] = []
        for result in results:
            if isinstance(result, ProxyEndpoint):
                validated.append(result)
        return validated

    async def _probe_socks5(self, endpoint: ProxyEndpoint) -> bool:
        """Cheap liveness probe: complete a SOCKS5 greeting, no destination.

        This performs no third-party request at all, which keeps validation
        private and fast. It cannot detect a proxy that accepts connections
        but cannot reach the internet; runtime failures cover that case.
        """

        timeout = self._settings.proxy_validation_timeout_s
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(endpoint.host, endpoint.port),
                timeout=timeout,
            )
            writer.write(_SOCKS5_GREETING)
            await asyncio.wait_for(writer.drain(), timeout=timeout)
            reply = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
            return reply[0:1] == b"\x05" and reply[1:2] in _SOCKS5_METHODS
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            return False
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, asyncio.TimeoutError):
                    pass

    async def _probe_http(self, endpoint: ProxyEndpoint) -> bool:
        """Optional end-to-end probe through the proxy (opt-in via config)."""

        try:
            async with httpx.AsyncClient(
                proxy=endpoint.url,
                timeout=self._settings.proxy_validation_timeout_s,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.get(
                    self._settings.proxy_validate_url,
                    headers={"User-Agent": self._settings.user_agent},
                )
                return response.status_code < 500
        except (httpx.HTTPError, ValueError, ImportError):
            return False

    # --- rotation and health ----------------------------------------------

    @property
    def size(self) -> int:
        return len(self._proxies)

    def available(self) -> int:
        now = time.monotonic()
        return sum(
            1
            for endpoint in self._proxies
            if self._cooldown_until.get(endpoint, 0.0) <= now
        )

    def _pick(self, excluded: set[ProxyEndpoint]) -> ProxyEndpoint | None:
        total = len(self._proxies)
        if total == 0:
            return None

        now = time.monotonic()
        for _ in range(total):
            endpoint = self._proxies[self._cursor % total]
            self._cursor = (self._cursor + 1) % total
            if endpoint in excluded:
                continue
            if self._cooldown_until.get(endpoint, 0.0) > now:
                continue
            return endpoint
        return None

    async def client_for(self, endpoint: ProxyEndpoint) -> httpx.AsyncClient:
        """Return the cached client for ``endpoint``, creating it on demand."""

        async with self._pool_lock:
            cached = self._clients.pop(endpoint, None)
            if cached is not None:
                # Re-insert to keep the dict in least-recently-used order.
                self._clients[endpoint] = cached
                return cached

            while len(self._clients) >= self._settings.proxy_max_clients:
                oldest = next(iter(self._clients))
                evicted = self._clients.pop(oldest)
                await evicted.aclose()

            client = httpx.AsyncClient(
                proxy=endpoint.url,
                timeout=httpx.Timeout(
                    self._settings.request_timeout_s,
                    connect=self._settings.proxy_connect_timeout_s,
                ),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                follow_redirects=False,
                # Ignore HTTP(S)_PROXY/ALL_PROXY from the environment: this
                # client must only ever use the SOCKS5 proxy above.
                trust_env=False,
            )
            self._clients[endpoint] = client
            return client

    def acquire_endpoint(
        self,
        *,
        exclude: set[ProxyEndpoint] | None = None,
    ) -> ProxyEndpoint | None:
        """Pick the next healthy proxy without creating an httpx client.

        Used by the Exa SDK path, which clones the SDK's own client (base URL
        and headers) onto the proxy instead of sharing the pool.
        """

        if not self.enabled:
            return None
        return self._pick(exclude or set())

    async def acquire(
        self,
        *,
        exclude: set[ProxyEndpoint] | None = None,
    ) -> tuple[ProxyEndpoint, httpx.AsyncClient] | None:
        """Pick the next healthy proxy and return it with its client."""

        endpoint = self.acquire_endpoint(exclude=exclude)
        if endpoint is None:
            return None

        try:
            client = await self.client_for(endpoint)
        except ImportError:
            # httpx needs the optional ``socksio`` dependency: warn once, not
            # on every provider request.
            if not self._socks_missing_warned:
                self._socks_missing_warned = True
                logger.warning(
                    "SOCKS5 support unavailable (reason=proxy_socksio_missing); "
                    "install httpx[socks]"
                )
            return None
        except ValueError:
            logger.warning("Invalid SOCKS5 proxy URL (reason=proxy_invalid_url)")
            self.mark_failure(endpoint, "proxy_invalid_url")
            return None
        return endpoint, client

    def mark_success(self, endpoint: ProxyEndpoint) -> None:
        self._failures.pop(endpoint, None)
        self._cooldown_until.pop(endpoint, None)

    def mark_failure(self, endpoint: ProxyEndpoint, reason: str) -> None:
        """Quarantine a proxy with exponential backoff; no address is logged."""

        failures = self._failures.get(endpoint, 0) + 1
        self._failures[endpoint] = failures
        cooldown = min(
            self._settings.proxy_cooldown_s * (2 ** (failures - 1)),
            self._settings.proxy_max_cooldown_s,
        )
        self._cooldown_until[endpoint] = time.monotonic() + cooldown
        # Never include the endpoint: only the reason and counters.
        logger.debug(
            "SOCKS5 proxy quarantined (reason=%s, failures=%d, cooldown_s=%.0f)",
            reason,
            failures,
            cooldown,
        )

    def status(self) -> dict[str, object]:
        """Aggregate, privacy-safe state for the health endpoint."""

        now = time.monotonic()
        age: float | None = None
        if self._last_refresh is not None:
            age = round(max(0.0, now - self._last_refresh), 1)

        return {
            "enabled": self.enabled,
            "pool": len(self._proxies),
            "available": self.available(),
            "quarantined": sum(
                1 for until in self._cooldown_until.values() if until > now
            ),
            "last_refresh_s": age,
            "last_error": self._last_error,
        }


class ProxiedHttpClient:
    """Request facade that rotates over the managed SOCKS5 proxies.

    It exposes only ``get``/``post``, mirroring the subset of
    ``httpx.AsyncClient`` used by the provider clients, so a test can inject a
    ``MockTransport``-backed client instead.
    """

    def __init__(
        self,
        manager: ProxyManager,
        *,
        direct: httpx.AsyncClient,
        settings: Settings,
    ) -> None:
        self.manager = manager
        self._direct = direct
        self._settings = settings
        self._last_warning = 0.0

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        return await self.request(
            "GET",
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        )

    async def post(
        self,
        url: str,
        *,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        return await self.request(
            "POST",
            url,
            json=json,
            headers=headers,
            timeout=timeout,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.Response:
        request_timeout = self._split_timeout(timeout)

        if not self._settings.proxy_enabled:
            return await self._direct.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
                timeout=request_timeout,
            )

        tried: set[ProxyEndpoint] = set()
        last_error: httpx.HTTPError | None = None
        reason = "proxy_pool_empty"

        for _ in range(self._settings.proxy_max_attempts):
            acquired = await self.manager.acquire(exclude=tried)
            if acquired is None:
                reason = "proxy_pool_empty" if not tried else "proxy_attempts_exhausted"
                break

            endpoint, client = acquired
            tried.add(endpoint)
            try:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=request_timeout,
                )
            except httpx.TransportError as exc:
                # Connection refused, timeout, proxy tunnel failure, ...
                # Drop this proxy and retry immediately with another one.
                self.manager.mark_failure(endpoint, type(exc).__name__.lower())
                last_error = exc
                reason = "proxy_attempts_exhausted"
                continue
            except ImportError:
                reason = "proxy_socksio_missing"
                break
            except BaseException:
                # Cancellation or programming error: never mask it.
                raise

            self.manager.mark_success(endpoint)
            return response

        if not self._settings.proxy_fallback_direct:
            if last_error is not None:
                raise last_error
            raise httpx.ProxyError("no_socks5_proxy_available")

        self._warn_fallback(reason)
        return await self._direct.request(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
            timeout=request_timeout,
        )

    def _split_timeout(
        self,
        timeout: float | httpx.Timeout | None,
    ) -> float | httpx.Timeout | None:
        """Keep the read timeout from the provider, shorten only the connect."""

        if timeout is None or isinstance(timeout, httpx.Timeout):
            return timeout
        connect = min(self._settings.proxy_connect_timeout_s, float(timeout))
        return httpx.Timeout(timeout, connect=connect)

    def _warn_fallback(self, reason: str) -> None:
        now = time.monotonic()
        if now - self._last_warning < self._settings.proxy_warning_interval_s:
            return
        self._last_warning = now
        # Reason code only: no proxy address, no destination, no query.
        logger.warning(
            "SOCKS5 proxy unavailable (reason=%s); using direct server connection",
            reason,
        )
