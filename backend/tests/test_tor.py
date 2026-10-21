from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.services.tor import TorCheck, TorStatusService

CHECK_URL = "https://check.torproject.org/api/ip"


def make_settings(**overrides: object) -> Settings:
    options: dict[str, object] = {
        "tor_enabled": True,
        "tor_proxy_url": "socks5://127.0.0.1:9050",
        "tor_status_url": CHECK_URL,
        "tor_timeout_s": 1.0,
        "tor_cache_ttl_s": 30.0,
    }
    options.update(overrides)
    return Settings(_env_file=None, **options)  # type: ignore[arg-type]


def factory_for(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[[], httpx.AsyncClient]:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


class TestParsing:
    async def test_active_exit_node(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"IsTor": True, "IP": "185.220.101.1"})

        service = TorStatusService(make_settings(), client_factory=factory_for(handler))
        result = await service.check()

        assert result.active is True
        assert result.exit_ip == "185.220.101.1"
        assert result.latency_ms is not None
        assert result.error is None

    async def test_reachable_but_not_tor(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"IsTor": False, "IP": "203.0.113.10"})

        service = TorStatusService(make_settings(), client_factory=factory_for(handler))
        result = await service.check()

        assert result.active is False
        assert result.error == "tor_not_an_exit"
        assert result.exit_ip == "203.0.113.10"

    async def test_unexpected_address_is_not_exposed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"IsTor": True, "IP": "<script>alert(1)</script>"})

        service = TorStatusService(make_settings(), client_factory=factory_for(handler))
        result = await service.check()

        assert result.active is True
        assert result.exit_ip is None

    async def test_http_error_returns_a_reason_code(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "down"})

        service = TorStatusService(make_settings(), client_factory=factory_for(handler))
        result = await service.check()

        assert result.active is False
        assert result.error == "tor_http_503"

    async def test_invalid_json(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not json")

        service = TorStatusService(make_settings(), client_factory=factory_for(handler))
        result = await service.check()

        assert result.active is False
        assert result.error == "tor_invalid_json"

    async def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        service = TorStatusService(make_settings(), client_factory=factory_for(handler))
        result = await service.check()

        assert result.active is False
        assert result.error == "tor_timeout"

    async def test_connection_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        service = TorStatusService(make_settings(), client_factory=factory_for(handler))
        result = await service.check()

        assert result.active is False
        assert result.error == "tor_unreachable"


class TestCaching:
    async def test_disabled_never_probes(self) -> None:
        calls = 0

        async def probe() -> TorCheck:
            nonlocal calls
            calls += 1
            return TorCheck(active=True)

        service = TorStatusService(make_settings(tor_enabled=False), probe=probe)
        result = await service.check()

        assert result.active is False
        assert result.error == "tor_disabled"
        assert calls == 0

    async def test_fresh_cache_avoids_second_probe(self) -> None:
        calls = 0

        async def probe() -> TorCheck:
            nonlocal calls
            calls += 1
            return TorCheck(active=True, exit_ip="185.220.101.1")

        service = TorStatusService(make_settings(tor_cache_ttl_s=30), probe=probe)

        first = await service.check()
        second = await service.check()

        assert first == second
        assert calls == 1

    async def test_zero_ttl_probes_every_time(self) -> None:
        calls = 0

        async def probe() -> TorCheck:
            nonlocal calls
            calls += 1
            return TorCheck(active=False, error="tor_timeout")

        service = TorStatusService(make_settings(tor_cache_ttl_s=0), probe=probe)

        await service.check()
        await service.check()

        assert calls == 2

    async def test_concurrent_checks_share_one_probe(self) -> None:
        calls = 0

        async def probe() -> TorCheck:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return TorCheck(active=True)

        service = TorStatusService(make_settings(tor_cache_ttl_s=30), probe=probe)

        results = await asyncio.gather(
            service.check(),
            service.check(),
            service.check(),
        )

        assert calls == 1
        assert all(result.active for result in results)


@pytest.mark.parametrize("address", ["", "not-an-ip", "1.2.3.4 " + "x" * 80])
def test_clean_ip_rejects_invalid_values(address: str) -> None:
    assert TorStatusService._clean_ip(address) is None
