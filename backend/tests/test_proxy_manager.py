from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.services import exa as exa_module
from app.services.errors import BackendError
from app.services.exa import ExaClient, _replace_http_clients
from app.services.proxy_manager import (
    ProxyEndpoint,
    ProxiedHttpClient,
    ProxyManager,
    parse_proxy_lines,
)


def make_settings(**overrides: object) -> Settings:
    options: dict[str, object] = {
        "proxy_enabled": True,
        "proxy_validate_on_load": False,
        "proxy_warning_interval_s": 0,
    }
    options.update(overrides)
    return Settings(_env_file=None, **options)  # type: ignore[arg-type]


# --- list parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1.2.3.4:1080", ("1.2.3.4", 1080, None, None)),
        ("socks5://1.2.3.4:1080", ("1.2.3.4", 1080, None, None)),
        ("socks5h://1.2.3.4:1080", ("1.2.3.4", 1080, None, None)),
        ("user:pass@1.2.3.4:1080", ("1.2.3.4", 1080, "user", "pass")),
        ("socks5://user:pass@1.2.3.4:1080", ("1.2.3.4", 1080, "user", "pass")),
        ("[2001:db8::1]:1080", ("2001:db8::1", 1080, None, None)),
    ],
)
def test_parse_proxy_lines_accepts_supported_forms(
    line: str,
    expected: tuple[str, int, str | None, str | None],
) -> None:
    proxies = parse_proxy_lines(line, limit=10)

    assert len(proxies) == 1
    proxy = proxies[0]
    assert (proxy.host, proxy.port, proxy.username, proxy.password) == expected


def test_parse_proxy_lines_skips_invalid_duplicate_and_foreign_entries() -> None:
    raw = "\n".join(
        [
            "# comment",
            "1.2.3.4:1080",
            "1.2.3.4:1080",
            "1.2.3.4:notaport",
            "1.2.3.4:70000",
            "http://1.2.3.4:8080",
            "2001:db8::1:1080",
            "",
        ]
    )

    proxies = parse_proxy_lines(raw, limit=10)

    assert proxies == [ProxyEndpoint("1.2.3.4", 1080)]


def test_parse_proxy_lines_respects_the_limit() -> None:
    raw = "\n".join(f"10.0.0.{i}:1080" for i in range(1, 30))

    assert len(parse_proxy_lines(raw, limit=5)) == 5


def test_proxy_endpoint_url_encodes_credentials_and_brackets_ipv6() -> None:
    proxy = ProxyEndpoint("2001:db8::1", 1080, username="user", password="p@ss word")

    assert proxy.url == "socks5://user:p%40ss%20word@[2001:db8::1]:1080"
    assert repr(proxy) == "<socks5-proxy>"


# --- rotation and health --------------------------------------------------


@pytest.mark.asyncio
async def test_round_robin_rotation_and_quarantine() -> None:
    async with httpx.AsyncClient() as http:
        manager = ProxyManager(make_settings(proxy_cooldown_s=60), http=http)

    first = ProxyEndpoint("10.0.0.1", 1080)
    second = ProxyEndpoint("10.0.0.2", 1080)
    manager._proxies = [first, second]

    assert manager._pick(set()) is first
    assert manager._pick(set()) is second
    assert manager._pick(set()) is first

    manager.mark_failure(first, "connecterror")

    assert manager._pick(set()) is second
    assert manager.available() == 1
    status = manager.status()
    assert status["pool"] == 2
    assert status["available"] == 1
    assert status["quarantined"] == 1
    assert status["last_error"] is None


@pytest.mark.asyncio
async def test_repeated_failures_extend_the_cooldown() -> None:
    async with httpx.AsyncClient() as http:
        manager = ProxyManager(
            make_settings(proxy_cooldown_s=10, proxy_max_cooldown_s=25),
            http=http,
        )

    proxy = ProxyEndpoint("10.0.0.1", 1080)
    manager._proxies = [proxy]

    for _ in range(5):
        manager.mark_failure(proxy, "readtimeout")

    deadline = manager._cooldown_until[proxy]
    remaining = deadline - time.monotonic()
    assert 0 < remaining <= 25


@pytest.mark.asyncio
async def test_refresh_downloads_and_replaces_the_pool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="1.1.1.1:1080\n2.2.2.2:1080\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        manager = ProxyManager(
            make_settings(proxy_pool_size=10, proxy_list_extra="3.3.3.3:1080"),
            http=http,
        )
        await manager.refresh()

    assert manager.size == 3
    assert manager.available() == 3
    status = manager.status()
    assert status["last_error"] is None
    assert isinstance(status["last_refresh_s"], float)
    assert status["last_refresh_s"] <= 0.5


@pytest.mark.asyncio
async def test_failed_refresh_keeps_the_cached_pool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        manager = ProxyManager(make_settings(), http=http)
        manager._proxies = [ProxyEndpoint("10.0.0.1", 1080)]
        await manager.refresh()

    assert manager.size == 1
    assert manager.status()["last_error"] == "proxy_list_http_503"


@pytest.mark.asyncio
async def test_acquire_returns_none_when_every_proxy_is_quarantined() -> None:
    async with httpx.AsyncClient() as http:
        manager = ProxyManager(make_settings(proxy_cooldown_s=600), http=http)

    proxy = ProxyEndpoint("10.0.0.1", 1080)
    manager._proxies = [proxy]
    manager.mark_failure(proxy, "connecterror")

    assert await manager.acquire() is None


# --- HTTP routing ---------------------------------------------------------


class FakeProxyManager:
    """Minimal stand-in for ProxyManager, with scripted acquire results."""

    enabled = True

    def __init__(self, script: list[tuple[ProxyEndpoint, object]]) -> None:
        self._script = list(script)
        self.failures: list[tuple[ProxyEndpoint, str]] = []
        self.successes: list[ProxyEndpoint] = []

    async def acquire(
        self,
        *,
        exclude: set[ProxyEndpoint] | None = None,
    ) -> tuple[ProxyEndpoint, object] | None:
        excluded = exclude or set()
        for endpoint, client in self._script:
            if endpoint not in excluded:
                return endpoint, client
        return None

    def mark_success(self, endpoint: ProxyEndpoint) -> None:
        self.successes.append(endpoint)

    def mark_failure(self, endpoint: ProxyEndpoint, reason: str) -> None:
        self.failures.append((endpoint, reason))


class FailingHttp:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls += 1
        raise self.error


class ReturningHttp:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls = 0

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls += 1
        return self.response


@pytest.mark.asyncio
async def test_transport_failure_rotates_then_falls_back_to_direct() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    first = ProxyEndpoint("10.0.0.1", 1080)
    second = ProxyEndpoint("10.0.0.2", 1080)
    manager = FakeProxyManager(
        [
            (first, FailingHttp(httpx.ConnectError("refused"))),
            (second, FailingHttp(httpx.ReadTimeout("slow"))),
        ]
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as direct:
        client = ProxiedHttpClient(
            manager,  # type: ignore[arg-type]
            direct=direct,
            settings=make_settings(),
        )
        response = await client.get("https://api.example.test/search", params={"q": "privacy"})

    assert response.status_code == 200
    assert seen == ["https://api.example.test/search?q=privacy"]
    assert [endpoint for endpoint, _ in manager.failures] == [first, second]
    assert manager.successes == []


@pytest.mark.asyncio
async def test_proxy_failure_never_falls_back_when_disabled() -> None:
    manager = FakeProxyManager(
        [(ProxyEndpoint("10.0.0.1", 1080), FailingHttp(httpx.ConnectError("refused")))]
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as direct:
        client = ProxiedHttpClient(
            manager,  # type: ignore[arg-type]
            direct=direct,
            settings=make_settings(proxy_fallback_direct=False),
        )
        with pytest.raises(httpx.ConnectError):
            await client.get("https://api.example.test/search")

    assert manager.failures


@pytest.mark.asyncio
async def test_http_error_response_is_not_treated_as_a_proxy_failure() -> None:
    endpoint = ProxyEndpoint("10.0.0.1", 1080)
    request = httpx.Request("GET", "https://api.example.test/search")
    manager = FakeProxyManager(
        [(endpoint, ReturningHttp(httpx.Response(429, request=request)))]
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as direct:
        client = ProxiedHttpClient(
            manager,  # type: ignore[arg-type]
            direct=direct,
            settings=make_settings(),
        )
        response = await client.get("https://api.example.test/search")

    assert response.status_code == 429
    assert manager.failures == []
    assert manager.successes == [endpoint]


@pytest.mark.asyncio
async def test_empty_pool_uses_direct_connection() -> None:
    manager = FakeProxyManager([])

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(204))) as direct:
        client = ProxiedHttpClient(
            manager,  # type: ignore[arg-type]
            direct=direct,
            settings=make_settings(),
        )
        response = await client.post("https://api.example.test/search", json={"q": "privacy"})

    assert response.status_code == 204


# --- Exa SDK integration --------------------------------------------------


class FakeExaSdk:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0

    async def search(self, query: str, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(results=[])


class EmptyProxyManager:
    enabled = True

    def acquire_endpoint(
        self,
        *,
        exclude: set[ProxyEndpoint] | None = None,
    ) -> ProxyEndpoint | None:
        return None

    def mark_success(self, endpoint: ProxyEndpoint) -> None:
        pass

    def mark_failure(self, endpoint: ProxyEndpoint, reason: str) -> None:
        pass


@pytest.mark.asyncio
async def test_replace_http_clients_swaps_the_sdk_transport() -> None:
    original = httpx.AsyncClient(
        base_url="https://api.exa.ai",
        headers={"x-api-key": "test-key"},
    )
    target = SimpleNamespace(client=original, other="value")

    replacement, replaced = _replace_http_clients(
        target,
        ProxyEndpoint("10.0.0.1", 1080),
    )

    assert replacement is not None
    assert target.client is replacement
    assert replaced == [original]
    assert target.other == "value"
    # The clone keeps the SDK configuration needed for relative paths and auth.
    assert str(replacement.base_url) == "https://api.exa.ai"
    assert replacement.headers["x-api-key"] == "test-key"
    await original.aclose()
    await replacement.aclose()


@pytest.mark.asyncio
async def test_exa_uses_direct_connection_when_the_pool_is_empty() -> None:
    sdk = FakeExaSdk()
    client = ExaClient(
        make_settings(exa_api_key="test-key"),
        sdk=sdk,  # type: ignore[arg-type]
        proxies=EmptyProxyManager(),  # type: ignore[arg-type]
    )

    result = await client.search("privacy", count=5, offset=0)

    assert sdk.calls == 1
    assert result.hits == []


@pytest.mark.asyncio
async def test_exa_quarantines_failing_proxies_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSdk:
        def __init__(self, **kwargs: object) -> None:
            # The real SDK also builds its own httpx client; the proxy
            # injection must replace it.
            self._client = httpx.AsyncClient()

        async def search(self, query: str, **kwargs: object) -> object:
            raise httpx.ConnectError("tunnel refused")

    monkeypatch.setattr(exa_module, "AsyncExa", FailingSdk)

    class ScriptedManager:
        enabled = True

        def __init__(self) -> None:
            self.endpoints = [
                ProxyEndpoint("10.0.0.1", 1080),
                ProxyEndpoint("10.0.0.2", 1080),
            ]
            self.failures: list[ProxyEndpoint] = []

        def acquire_endpoint(
            self,
            *,
            exclude: set[ProxyEndpoint] | None = None,
        ) -> ProxyEndpoint | None:
            excluded = exclude or set()
            for endpoint in self.endpoints:
                if endpoint not in excluded:
                    return endpoint
            return None

        def mark_success(self, endpoint: ProxyEndpoint) -> None:
            pass

        def mark_failure(self, endpoint: ProxyEndpoint, reason: str) -> None:
            self.failures.append(endpoint)

    manager = ScriptedManager()
    direct_sdk = FakeExaSdk()
    client = ExaClient(
        make_settings(exa_api_key="test-key", proxy_max_attempts=2),
        sdk=direct_sdk,  # type: ignore[arg-type]
        proxies=manager,  # type: ignore[arg-type]
    )

    result = await client.search("privacy", count=5, offset=0)

    # Two different proxies were tried, both quarantined, then the SDK ran
    # directly so the user still gets an answer.
    assert manager.failures == manager.endpoints
    assert direct_sdk.calls == 1
    assert result.hits == []


@pytest.mark.asyncio
async def test_exa_surfaces_the_transport_error_when_direct_fallback_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSdk:
        def __init__(self, **kwargs: object) -> None:
            self._client = httpx.AsyncClient()

        async def search(self, query: str, **kwargs: object) -> object:
            raise httpx.ConnectError("tunnel refused")

    monkeypatch.setattr(exa_module, "AsyncExa", FailingSdk)

    class SingleProxyManager:
        enabled = True

        def acquire_endpoint(
            self,
            *,
            exclude: set[ProxyEndpoint] | None = None,
        ) -> ProxyEndpoint | None:
            return None

        def mark_success(self, endpoint: ProxyEndpoint) -> None:
            pass

        def mark_failure(self, endpoint: ProxyEndpoint, reason: str) -> None:
            pass

    client = ExaClient(
        make_settings(
            exa_api_key="test-key",
            proxy_fallback_direct=False,
        ),
        sdk=FakeExaSdk(),  # type: ignore[arg-type]
        proxies=SingleProxyManager(),  # type: ignore[arg-type]
    )

    with pytest.raises(BackendError) as exc_info:
        await client.search("privacy", count=5, offset=0)

    # No proxy was available at all, so the SDK must not be used directly.
    assert exc_info.value.reason == "exa_proxyerror"
