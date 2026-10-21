from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from exa_py import AsyncExa

from app.config import Settings
from app.services.errors import BackendError
from app.services.exa import ExaClient


def exa_result(
    url: str,
    *,
    title: str | None = None,
    text: str | None = None,
    highlights: list[str] | None = None,
    summary: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        url=url,
        title=title,
        text=text,
        highlights=highlights or [],
        summary=summary,
    )


class FakeExaSdk:
    def __init__(
        self,
        results: list[SimpleNamespace] | None = None,
        *,
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.results = results or []
        self.error = error
        self.delay = delay
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def search(self, query: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append((query, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(results=self.results)


def test_exa_client_initializes_the_async_sdk() -> None:
    settings = Settings(_env_file=None, exa_api_key="test-key")

    client = ExaClient(settings)

    assert isinstance(client._sdk, AsyncExa)


@pytest.mark.asyncio
async def test_exa_search_uses_the_official_sdk_and_parses_results() -> None:
    sdk = FakeExaSdk(
        [
            exa_result(
                "https://www.example.com/privacy/?utm_source=newsletter",
                title="<b>Guida</b> alla privacy",
                text="Testo <i>pulito</i> con   spazi.",
                highlights=["Primo <b>estratto</b>", "Secondo estratto"],
            ),
            exa_result(
                "https://second.example/page",
                title="",
                text="",
                highlights=["Solo highlight"],
            ),
        ]
    )
    settings = Settings(
        _env_file=None,
        exa_api_key="test-key",
        exa_search_type="fast",
        exa_moderation=False,
        exa_timeout_s=7.0,
    )

    client = ExaClient(settings, sdk=sdk)  # type: ignore[arg-type]
    result = await client.search("privacy", count=10, offset=0)

    assert len(sdk.calls) == 1
    query, kwargs = sdk.calls[0]
    assert query == "privacy"
    assert kwargs["num_results"] == 10
    assert kwargs["type"] == "fast"
    assert kwargs["moderation"] is False
    assert kwargs["contents"] == {
        "text": {"max_characters": 1_200},
        "highlights": True,
    }

    assert len(result.hits) == 2
    first = result.hits[0]
    assert first.title == "Guida alla privacy"
    assert first.url == "https://www.example.com/privacy"
    assert first.snippet == "Testo pulito con spazi."
    assert first.extra_snippets == ("Primo estratto", "Secondo estratto")

    second = result.hits[1]
    assert second.title == "second.example"
    assert second.snippet == "Solo highlight"


@pytest.mark.asyncio
async def test_exa_summary_is_used_as_insight_when_enabled() -> None:
    sdk = FakeExaSdk(
        [
            exa_result(
                "https://example.com/privacy",
                title="Privacy",
                text="Testo della pagina",
                summary="Sintesi <b>Exa</b> della pagina.",
            )
        ]
    )
    settings = Settings(
        _env_file=None,
        exa_api_key="test-key",
        exa_include_summary=True,
    )

    result = await ExaClient(settings, sdk=sdk).search("privacy", count=10, offset=0)  # type: ignore[arg-type]

    assert sdk.calls[0][1]["contents"]["summary"] is True
    assert len(result.insights) == 1
    assert result.insights[0].source == "exa"
    assert result.insights[0].text == "Sintesi Exa della pagina."
    assert result.insights[0].url == "https://example.com/privacy"


@pytest.mark.asyncio
async def test_exa_emulates_offset_and_reports_more_results() -> None:
    sdk = FakeExaSdk(
        [
            exa_result(f"https://example.com/{i}", title=f"Result {i}", text="text")
            for i in range(20)
        ]
    )
    settings = Settings(_env_file=None, exa_api_key="test-key")

    result = await ExaClient(settings, sdk=sdk).search("privacy", count=10, offset=10)  # type: ignore[arg-type]

    assert sdk.calls[0][1]["num_results"] == 20
    assert [hit.url for hit in result.hits] == [f"https://example.com/{i}" for i in range(10, 20)]
    assert result.has_more is True


@pytest.mark.asyncio
async def test_exa_rate_limit_is_reported_as_reason_code() -> None:
    sdk = FakeExaSdk(error=ValueError("Request failed with status code 429: rate limited"))
    settings = Settings(_env_file=None, exa_api_key="test-key")

    with pytest.raises(BackendError) as exc_info:
        await ExaClient(settings, sdk=sdk).search("privacy", count=10, offset=0)  # type: ignore[arg-type]

    assert exc_info.value.reason == "exa_rate_limited"


@pytest.mark.asyncio
async def test_exa_http_status_is_mapped_to_a_reason_code() -> None:
    request = httpx.Request("POST", "https://api.exa.ai/search")
    response = httpx.Response(403, request=request)
    sdk = FakeExaSdk(error=httpx.HTTPStatusError("forbidden", request=request, response=response))
    settings = Settings(_env_file=None, exa_api_key="test-key")

    with pytest.raises(BackendError) as exc_info:
        await ExaClient(settings, sdk=sdk).search("privacy", count=10, offset=0)  # type: ignore[arg-type]

    assert exc_info.value.reason == "exa_forbidden"


@pytest.mark.asyncio
async def test_exa_timeout_is_reported_as_reason_code() -> None:
    sdk = FakeExaSdk(error=httpx.ReadTimeout("timed out"))
    settings = Settings(_env_file=None, exa_api_key="test-key")

    with pytest.raises(BackendError) as exc_info:
        await ExaClient(settings, sdk=sdk).search("privacy", count=10, offset=0)  # type: ignore[arg-type]

    assert exc_info.value.reason == "exa_timeout"


@pytest.mark.asyncio
async def test_exa_timeout_is_enforced_by_the_client() -> None:
    sdk = FakeExaSdk(delay=1.0)
    settings = Settings(_env_file=None, exa_api_key="test-key", exa_timeout_s=0.01)

    with pytest.raises(BackendError) as exc_info:
        await ExaClient(settings, sdk=sdk).search("privacy", count=10, offset=0)  # type: ignore[arg-type]

    assert exc_info.value.reason == "exa_timeout"


@pytest.mark.asyncio
async def test_exa_without_key_is_disabled() -> None:
    settings = Settings(_env_file=None, exa_api_key="")
    client = ExaClient(settings)

    assert client.configured is False
    result = await client.search("privacy", count=10, offset=0)
    assert result.hits == []
    assert result.total is None
