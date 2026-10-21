from __future__ import annotations

import pytest

from app.api.routes.suggest import MAX_SUGGESTIONS, suggest
from app.config import Settings
from app.services.errors import BackendError
from app.services.results import RawHit
from app.services.search import SearchService


def hit(url: str, title: str = "", domain: str = "example.com") -> RawHit:
    return RawHit(url=url, title=title or url, domain=domain)


class FakeLocal:
    def __init__(self, hits: list[RawHit] | None = None, error: str | None = None) -> None:
        self.hits = hits or []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    async def suggest(self, query: str, *, limit: int = 6) -> list[RawHit]:
        self.calls.append((query, limit))
        if self.error:
            raise BackendError(self.error)
        return self.hits[:limit]

    async def ping(self) -> bool:
        return self.error is None


class FakeRequest:
    def __init__(self, service: SearchService) -> None:
        self.app = type("App", (), {"state": type("State", (), {"search_service": service})()})


def build_service(local: FakeLocal) -> SearchService:
    settings = Settings(_env_file=None, brave_api_key="")
    return SearchService(  # type: ignore[arg-type]
        settings=settings,
        local=local,
        brave=object(),
    )


@pytest.mark.asyncio
async def test_route_returns_local_suggestions() -> None:
    local = FakeLocal(
        [
            hit("https://example.com/auto", "Auto usata"),
            hit("https://example.com/auto", "Duplicato"),
            hit("https://other.example/moto", "Moto usata", domain="other.example"),
        ]
    )

    response = await suggest(FakeRequest(build_service(local)), "au")  # type: ignore[arg-type]

    assert local.calls == [("au", MAX_SUGGESTIONS)]
    assert response.query == "au"
    assert [(item.title, item.domain) for item in response.suggestions] == [
        ("Auto usata", "example.com"),
        ("Moto usata", "other.example"),
    ]


@pytest.mark.asyncio
async def test_route_strips_operators_before_the_search() -> None:
    local = FakeLocal([hit("https://example.com/auto", "Auto usata")])

    response = await suggest(FakeRequest(build_service(local)), "site:example.com auto -spam")  # type: ignore[arg-type]

    assert local.calls == [("auto", MAX_SUGGESTIONS)]
    assert response.query == "auto"


@pytest.mark.asyncio
async def test_route_skips_the_index_for_an_operator_only_query() -> None:
    local = FakeLocal([hit("https://example.com/auto", "Auto usata")])

    response = await suggest(FakeRequest(build_service(local)), "-spam")  # type: ignore[arg-type]

    assert local.calls == []
    assert response.suggestions == []
    assert response.query == ""


@pytest.mark.asyncio
async def test_route_degrades_to_an_empty_dropdown() -> None:
    local = FakeLocal(error="meilisearch_connecterror")

    response = await suggest(FakeRequest(build_service(local)), "au")  # type: ignore[arg-type]

    assert response.suggestions == []


@pytest.mark.asyncio
async def test_suggestions_are_capped() -> None:
    local = FakeLocal([hit(f"https://example.com/{index}", f"Page {index}") for index in range(10)])

    response = await suggest(FakeRequest(build_service(local)), "pa")  # type: ignore[arg-type]

    assert len(response.suggestions) == MAX_SUGGESTIONS
