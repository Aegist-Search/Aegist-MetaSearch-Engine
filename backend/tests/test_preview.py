from __future__ import annotations

import httpx
import pytest

from app.services.preview import MAX_IMAGE_BYTES, PreviewService, is_public_image_url

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def png_response() -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG)


class TestSsrfGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/cover.png",
            "http://127.0.0.1:7700/cover.png",
            "http://10.1.2.3/cover.png",
            "http://172.16.0.10/cover.png",
            "http://192.168.1.10/cover.png",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0/cover.png",
            "http://[::1]/cover.png",
            "http://[::ffff:127.0.0.1]/cover.png",
            "file:///etc/passwd",
            "ftp://example.com/cover.png",
            "javascript:alert(1)",
            "http://8.8.8.8:notaport/cover.png",
            "http://[::1/cover.png",
            "",
        ],
    )
    async def test_blocks_non_public_targets(self, url: str) -> None:
        assert await is_public_image_url(url) is False

    async def test_allows_public_address(self) -> None:
        assert await is_public_image_url("https://8.8.8.8/cover.png") is True


class TestPinnedResolution:
    async def test_connects_to_the_validated_ip(self, monkeypatch) -> None:
        async def fake_resolve(host: str, port: int) -> tuple[str, ...]:
            return ("93.184.216.34",)

        monkeypatch.setattr("app.utils.net._resolve", fake_resolve)

        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return png_response()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = PreviewService(client, timeout_s=2.0)
            image = await service.fetch("https://images.example.com/cover.png")

        assert image is not None
        request = requests[0]
        assert str(request.url) == "https://93.184.216.34/cover.png"
        assert request.headers["host"] == "images.example.com"
        assert request.extensions["sni_hostname"] == "images.example.com"

    async def test_rejects_a_hostname_that_resolves_to_a_private_address(self, monkeypatch) -> None:
        async def fake_resolve(host: str, port: int) -> tuple[str, ...]:
            return ("127.0.0.1",)

        monkeypatch.setattr("app.utils.net._resolve", fake_resolve)

        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return png_response()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = PreviewService(client, timeout_s=2.0)
            assert await service.fetch("https://images.example.com/cover.png") is None

        assert requests == []

    async def test_revalidates_a_redirect_host_before_following_it(self, monkeypatch) -> None:
        async def fake_resolve(host: str, port: int) -> tuple[str, ...]:
            return {
                "images.example.com": ("93.184.216.34",),
                "internal.example": ("10.0.0.5",),
            }[host]

        monkeypatch.setattr("app.utils.net._resolve", fake_resolve)

        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://internal.example/secret.png"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = PreviewService(client, timeout_s=2.0)
            assert await service.fetch("https://images.example.com/cover.png") is None

        assert len(requests) == 1
        assert str(requests[0].url) == "https://93.184.216.34/cover.png"

    async def test_tries_the_other_validated_address(self, monkeypatch) -> None:
        async def fake_resolve(host: str, port: int) -> tuple[str, ...]:
            return ("93.184.216.34", "93.184.216.35")

        monkeypatch.setattr("app.utils.net._resolve", fake_resolve)

        attempts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(str(request.url))
            if len(attempts) == 1:
                raise httpx.ConnectError("boom", request=request)
            return png_response()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = PreviewService(client, timeout_s=2.0)
            image = await service.fetch("https://images.example.com/cover.png")

        assert image is not None
        assert attempts == [
            "https://93.184.216.34/cover.png",
            "https://93.184.216.35/cover.png",
        ]


class TestPreviewService:
    async def test_downloads_and_caches(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return png_response()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = PreviewService(client, timeout_s=2.0)
            first = await service.fetch("https://8.8.8.8/cover.png")
            second = await service.fetch("https://8.8.8.8/cover.png")

        assert first is not None
        assert first.content == PNG
        assert first.content_type == "image/png"
        assert second is first
        assert len(calls) == 1

    async def test_rejects_non_image_content_type(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = PreviewService(client, timeout_s=2.0)
            assert await service.fetch("https://8.8.8.8/cover.png") is None

    async def test_rejects_oversized_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_IMAGE_BYTES
            return httpx.Response(200, headers={"content-type": "image/png"}, content=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = PreviewService(client, timeout_s=2.0)
            assert await service.fetch("https://8.8.8.8/cover.png") is None

    async def test_does_not_follow_redirect_to_private_host(self) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret.png"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = PreviewService(client, timeout_s=2.0)
            assert await service.fetch("https://8.8.8.8/cover.png") is None

        assert len(calls) == 1

    @pytest.mark.parametrize(
        "url",
        ["", "javascript:alert(1)", "https://8.8.8.8/a b.png", "https://8.8.8.8/" + "a" * 2100],
    )
    async def test_rejects_unusable_urls_without_request(self, url: str) -> None:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return png_response()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = PreviewService(client, timeout_s=2.0)
            assert await service.fetch(url) is None

        assert calls == []
