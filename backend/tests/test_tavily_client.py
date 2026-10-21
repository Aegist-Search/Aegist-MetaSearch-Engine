from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.services.errors import BackendError
from app.services.tavily import TavilyClient


@pytest.mark.asyncio
async def test_tavily_request_and_result_parsing() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = request.content
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "<b>Guida</b> alla privacy",
                        "url": "https://www.example.com/privacy/?utm_source=x",
                        "content": "Contenuto <i>pulito</i> con   spazi.",
                    },
                    {
                        "title": "Seconda pagina",
                        "url": "https://second.example/page",
                        "content": "",
                    },
                ]
            },
        )

    settings = Settings(
        _env_file=None,
        tavily_api_key="test-key",
        tavily_language="IT",
        tavily_safe_search=True,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await TavilyClient(http, settings).search("privacy", count=2, offset=0)

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["authorization"] == "Bearer test-key"
    body = json.loads(captured["payload"])  # type: ignore[arg-type]
    assert body["query"] == "privacy"
    assert body["max_results"] == 2
    assert body["safe_search"] is True
    assert body["language"] == "it"
    assert body["include_answer"] is False

    assert len(result.hits) == 2
    assert result.hits[0].title == "Guida alla privacy"
    assert result.hits[0].url == "https://www.example.com/privacy"
    assert result.hits[0].snippet == "Contenuto pulito con spazi."
    # Tavily keeps the provider title even when it sends no content; the
    # domain is only a fallback for a missing title.
    assert result.hits[1].title == "Seconda pagina"
    assert result.hits[1].snippet == ""


@pytest.mark.asyncio
async def test_tavily_falls_back_to_the_domain_without_a_title() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "", "url": "https://second.example/page", "content": ""}
                ]
            },
        )

    settings = Settings(_env_file=None, tavily_api_key="test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await TavilyClient(http, settings).search("privacy", count=1, offset=0)

    assert result.hits[0].title == "second.example"


@pytest.mark.asyncio
async def test_tavily_answer_and_raw_content_are_exposed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["include_answer"] is True
        assert body["include_raw_content"] is True
        return httpx.Response(
            200,
            json={
                "answer": "Risposta <b>Tavily</b>.",
                "results": [
                    {
                        "title": "Pagina",
                        "url": "https://example.com/page",
                        "content": "Snippet.",
                        "raw_content": "Contenuto <i>esteso</i>.",
                    }
                ],
            },
        )

    settings = Settings(
        _env_file=None,
        tavily_api_key="test-key",
        tavily_include_answer=True,
        tavily_include_raw_content=True,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await TavilyClient(http, settings).search("privacy", count=10, offset=0)

    assert len(result.insights) == 1
    assert result.insights[0].source == "tavily"
    assert result.insights[0].text == "Risposta Tavily."
    assert result.hits[0].extra_snippets == ("Contenuto esteso.",)


@pytest.mark.asyncio
async def test_tavily_emulates_offset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["max_results"] == 4
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": f"Result {i}", "url": f"https://example.com/{i}", "content": "text"}
                    for i in range(4)
                ]
            },
        )

    settings = Settings(_env_file=None, tavily_api_key="test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await TavilyClient(http, settings).search("privacy", count=2, offset=2)

    assert [hit.url for hit in result.hits] == ["https://example.com/2", "https://example.com/3"]


@pytest.mark.asyncio
async def test_tavily_plan_limit_is_reported_as_reason_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(432, json={"detail": "plan limit"})

    settings = Settings(_env_file=None, tavily_api_key="test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BackendError) as exc_info:
            await TavilyClient(http, settings).search("privacy", count=10, offset=0)

    assert exc_info.value.reason == "tavily_plan_limit"
