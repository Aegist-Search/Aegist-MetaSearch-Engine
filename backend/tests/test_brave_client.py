from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.services.brave import BraveClient


@pytest.mark.asyncio
async def test_brave_request_targeting_and_extra_snippets() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        captured["token"] = request.headers.get("x-subscription-token")
        return httpx.Response(
            200,
            json={
                "query": {"more_results_available": True},
                "web": {
                    "results": [
                        {
                            "title": "<strong>Guida</strong> alla privacy",
                            "url": "https://www.example.com/privacy",
                            "description": "Descrizione <b>principale</b>.",
                            "extra_snippets": [
                                "Primo estratto",
                                "<i>Secondo</i> estratto",
                            ],
                        },
                        {
                            "title": "Senza descrizione",
                            "url": "https://example.com/seconda",
                            "description": "",
                            "extra_snippets": ["Estratto di fallback"],
                        },
                    ]
                },
            },
        )

    settings = Settings(
        _env_file=None,
        brave_api_key="test-key",
        brave_country="it",
        brave_search_lang="IT",
        brave_ui_lang="it-IT",
        brave_extra_snippets=True,
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BraveClient(http, settings)
        result = await client.search("privacy", count=10, offset=0)

    params = captured["params"]
    assert isinstance(params, dict)
    assert params["q"] == "privacy"
    assert params["count"] == "10"
    assert params["offset"] == "0"
    assert params["country"] == "IT"
    assert params["search_lang"] == "it"
    assert params["ui_lang"] == "it-IT"
    assert params["extra_snippets"] == "true"
    assert captured["token"] == "test-key"

    assert result.has_more is True
    assert len(result.hits) == 2

    first = result.hits[0]
    assert first.title == "Guida alla privacy"
    assert first.url == "https://www.example.com/privacy"
    assert first.snippet == "Descrizione principale."
    assert first.extra_snippets == ("Primo estratto", "Secondo estratto")

    second = result.hits[1]
    assert second.snippet == "Estratto di fallback"


@pytest.mark.asyncio
async def test_brave_infobox_is_used_as_insight() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "web": {"results": []},
                "infobox": {
                    "title": "Privacy",
                    "long_desc": "Panoramica <b>della</b> privacy.",
                    "website": "https://example.com/privacy",
                },
            },
        )

    settings = Settings(_env_file=None, brave_api_key="test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await BraveClient(http, settings).search("privacy", count=10, offset=0)

    assert len(result.insights) == 1
    assert result.insights[0].source == "brave"
    assert result.insights[0].title == "Privacy"
    assert result.insights[0].text == "Panoramica della privacy."
    assert result.insights[0].url == "https://example.com/privacy"
