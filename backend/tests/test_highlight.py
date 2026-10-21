from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.services.meili import MeilisearchClient
from app.utils.highlight import HighlightSpan, parse_highlighted


class TestParseHighlighted:
    def test_splits_balanced_matches(self) -> None:
        assert parse_highlighted("Auto <em>usata</em> a Roma") == (
            HighlightSpan("Auto ", False),
            HighlightSpan("usata", True),
            HighlightSpan(" a Roma", False),
        )

    def test_multiple_matches(self) -> None:
        assert parse_highlighted("<em>auto</em> e <em>moto</em>") == (
            HighlightSpan("auto", True),
            HighlightSpan(" e ", False),
            HighlightSpan("moto", True),
        )

    def test_adjacent_matches_are_merged(self) -> None:
        assert parse_highlighted("<em>auto</em><em>moto</em>") == (
            HighlightSpan("automoto", True),
        )

    def test_returns_empty_without_matches(self) -> None:
        assert parse_highlighted("Auto usata") == ()
        assert parse_highlighted("") == ()
        assert parse_highlighted(None) == ()

    def test_unmatched_tag_is_left_as_literal_text(self) -> None:
        assert parse_highlighted("prezzo <em> 100") == ()

    def test_nested_tags_do_not_break_the_spans(self) -> None:
        spans = parse_highlighted("<em>a<em>b</em>c</em>d")

        assert spans[0] == HighlightSpan("a<em>b</em>c", True)
        assert spans[1] == HighlightSpan("d", False)


@pytest.mark.asyncio
async def test_meili_search_requests_and_maps_highlights() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "id": "abc",
                        "title": "Auto usata",
                        "url": "https://example.com/auto",
                        "snippet": "Una auto usata a Roma",
                        "h1": "Auto",
                        "domain": "example.com",
                        "_rankingScore": 0.93,
                        "_formatted": {
                            "title": "<em>Auto</em> usata",
                            "snippet": "Una <em>auto</em> usata a Roma",
                        },
                    }
                ],
                "estimatedTotalHits": 1,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeilisearchClient(
            http,
            Settings(_env_file=None, meili_url="http://meili.test"),
        )
        result = await client.search("auto", limit=10, offset=0)

    assert captured["attributesToHighlight"] == ["title", "snippet"]
    assert captured["highlightPreTag"] == "<em>"
    assert captured["highlightPostTag"] == "</em>"

    hit = result.hits[0]
    assert hit.title == "Auto usata"
    assert hit.snippet == "Una auto usata a Roma"
    assert hit.title_highlight == (
        HighlightSpan("Auto", True),
        HighlightSpan(" usata", False),
    )
    assert hit.snippet_highlight == (
        HighlightSpan("Una ", False),
        HighlightSpan("auto", True),
        HighlightSpan(" usata a Roma", False),
    )


@pytest.mark.asyncio
async def test_meili_hit_without_formatted_values_has_no_highlights() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "id": "abc",
                        "title": "Auto usata",
                        "url": "https://example.com/auto",
                        "snippet": "Una auto usata",
                        "domain": "example.com",
                    }
                ],
                "estimatedTotalHits": 1,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MeilisearchClient(
            http,
            Settings(_env_file=None, meili_url="http://meili.test"),
        )
        result = await client.search("auto", limit=10, offset=0)

    assert result.hits[0].title_highlight == ()
    assert result.hits[0].snippet_highlight == ()
