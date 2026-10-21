from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.services.errors import BackendError
from app.services.serper import SerperClient


@pytest.mark.asyncio
async def test_serper_request_targeting_and_pagination() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("x-api-key")
        captured["payload"] = request.content
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "<b>Guida</b> alla privacy",
                        "link": "https://www.example.com/privacy/?utm_source=x",
                        "snippet": "Descrizione <i>principale</i>.",
                    },
                    {
                        "title": "Seconda pagina",
                        "link": "https://second.example/page",
                        "snippet": "",
                    },
                ],
                "answerBox": {
                    "title": "Privacy",
                    "answer": "Risposta <b>Serper</b>.",
                    "link": "https://example.com/privacy",
                },
                "knowledgeGraph": {
                    "title": "Privacy",
                    "description": "Descrizione <i>KG</i>.",
                    "website": "https://example.com/kg",
                },
            },
        )

    settings = Settings(
        _env_file=None,
        serper_api_key="test-key",
        serper_gl="IT",
        serper_hl="IT",
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await SerperClient(http, settings).search("privacy", count=2, offset=2)

    assert captured["url"] == "https://google.serper.dev/search"
    assert captured["key"] == "test-key"
    body = json.loads(captured["payload"])  # type: ignore[arg-type]
    assert body["q"] == "privacy"
    assert body["num"] == 2
    assert body["page"] == 2
    assert body["gl"] == "it"
    assert body["hl"] == "it"

    assert len(result.hits) == 2
    assert result.hits[0].title == "Guida alla privacy"
    assert result.hits[0].url == "https://www.example.com/privacy"
    assert result.hits[0].snippet == "Descrizione principale."
    assert result.has_more is True

    assert [insight.source for insight in result.insights] == ["serper", "serper"]
    assert result.insights[0].title == "Privacy"
    assert result.insights[0].text == "Risposta Serper."
    assert result.insights[0].url == "https://example.com/privacy"
    assert result.insights[1].text == "Descrizione KG."
    assert result.insights[1].url == "https://example.com/kg"


@pytest.mark.asyncio
async def test_serper_rate_limit_is_reported_as_reason_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "rate limited"})

    settings = Settings(_env_file=None, serper_api_key="test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(BackendError) as exc_info:
            await SerperClient(http, settings).search("privacy", count=10, offset=0)

    assert exc_info.value.reason == "serper_rate_limited"
