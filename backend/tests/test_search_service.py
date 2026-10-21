from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

import pytest

from app.config import Settings
from app.services.errors import BackendError
from app.services.results import BackendResult, RawHit, RawInsight
from app.services.search import SearchService, SearchUnavailable
from app.utils.highlight import HighlightSpan


def hit(
    url: str,
    title: str | None = None,
    *,
    domain: str = "",
    snippet: str = "snippet",
    extra_snippets: tuple[str, ...] = (),
    title_highlight: tuple[HighlightSpan, ...] = (),
    snippet_highlight: tuple[HighlightSpan, ...] = (),
    rank: float | None = None,
    crawled_at: int = 0,
) -> RawHit:
    return RawHit(
        url=url,
        title=title or url,
        snippet=snippet,
        domain=domain,
        extra_snippets=extra_snippets,
        title_highlight=title_highlight,
        snippet_highlight=snippet_highlight,
        rank=rank,
        crawled_at=crawled_at,
    )


class FakeLocal:
    def __init__(
        self,
        hits: list[RawHit] | None = None,
        total: int | None = None,
        error: str | None = None,
        suggestions: list[RawHit] | None = None,
        index_empty: bool = False,
        corrections: list[str] | None = None,
    ) -> None:
        self.hits = hits or []
        self.total = total if total is not None else len(self.hits)
        self.error = error
        self.suggestions = suggestions or []
        self.index_empty = index_empty
        self.corrections = corrections or []
        self.correction_calls: list[str] = []
        self.calls: list[tuple[str, int, int]] = []
        self.sorts: list[list[str] | None] = []
        self.filters: list[str | None] = []
        self.suggest_calls: list[tuple[str, int]] = []
        self.index_empty_calls = 0

    async def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int,
        sort: list[str] | None = None,
        filter: str | None = None,
    ) -> BackendResult:
        self.calls.append((query, limit, offset))
        self.sorts.append(sort)
        self.filters.append(filter)
        if self.error:
            raise BackendError(self.error)
        return BackendResult(hits=self.hits[offset : offset + limit], total=self.total)

    async def suggest(self, query: str, *, limit: int = 6) -> list[RawHit]:
        self.suggest_calls.append((query, limit))
        if self.error:
            raise BackendError(self.error)
        return self.suggestions[:limit]

    async def index_is_empty(self) -> bool:
        self.index_empty_calls += 1
        return self.index_empty

    async def matched_words(self, term: str, *, limit: int = 5) -> list[str]:
        self.correction_calls.append(term)
        return self.corrections[:limit]

    async def ping(self) -> bool:
        return self.error is None


class FakeBrave:
    def __init__(
        self,
        hits: list[RawHit] | None = None,
        *,
        has_more: bool = False,
        error: str | None = None,
        insights: tuple[RawInsight, ...] = (),
    ) -> None:
        self.hits = hits or []
        self.has_more = has_more
        self.error = error
        self.insights = insights
        self.calls: list[tuple[str, int, int]] = []

    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        self.calls.append((query, count, offset))
        if self.error:
            raise BackendError(self.error)
        return BackendResult(
            hits=self.hits[:count],
            total=None,
            has_more=self.has_more,
            insights=self.insights,
        )


class FakeFallback:
    def __init__(
        self,
        name: str = "exa",
        hits: list[RawHit] | None = None,
        *,
        has_more: bool = False,
        error: str | None = None,
        configured: bool = True,
        insights: tuple[RawInsight, ...] = (),
    ) -> None:
        self.name = name
        self.hits = hits or []
        self.has_more = has_more
        self.error = error
        self._configured = configured
        self.insights = insights
        self.calls: list[tuple[str, int, int]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        self.calls.append((query, count, offset))
        if self.error:
            raise BackendError(self.error)
        return BackendResult(
            hits=self.hits[:count],
            total=None,
            has_more=self.has_more,
            insights=self.insights,
        )


class TimeoutFallback(FakeFallback):
    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        raise asyncio.TimeoutError


class CrashFallback(FakeFallback):
    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        raise RuntimeError("provider exploded")


class SlowBrave(FakeBrave):
    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        await asyncio.sleep(5.0)
        return await super().search(query, count=count, offset=offset)


class SlowFallback(FakeFallback):
    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        await asyncio.sleep(5.0)
        return await super().search(query, count=count, offset=offset)


class BrokenWordsLocal(FakeLocal):
    async def matched_words(self, term: str, *, limit: int = 5) -> list[str]:
        raise BackendError("meilisearch_connecterror")


def build_service(
    local: FakeLocal,
    brave: FakeBrave,
    *,
    fallbacks: Sequence[FakeFallback] = (),
    **overrides: object,
) -> SearchService:
    options: dict[str, object] = {
        "brave_api_key": "test-key",
        "page_size": 10,
        "min_local_results": 3,
    }
    options.update(overrides)
    settings = Settings(_env_file=None, **options)  # type: ignore[arg-type]
    return SearchService(  # type: ignore[arg-type]
        settings=settings,
        local=local,
        brave=brave,
        fallbacks=fallbacks,
    )


@pytest.mark.asyncio
async def test_local_results_are_used_when_sufficient() -> None:
    local = FakeLocal([hit(f"https://example.com/{i}") for i in range(5)])
    brave = FakeBrave([hit("https://brave.example/fallback")])

    response = await build_service(local, brave).search("privacy")

    assert response.source == "local"
    assert len(response.results) == 5
    assert brave.calls == []
    assert response.pagination.total == 5
    assert response.pagination.total_pages == 1
    assert response.pagination.has_more is False
    assert response.meta.degraded is False


@pytest.mark.asyncio
async def test_exactly_three_local_results_skip_brave() -> None:
    local = FakeLocal([hit(f"https://example.com/{i}") for i in range(3)])
    brave = FakeBrave()

    response = await build_service(local, brave).search("privacy")

    assert brave.calls == []
    assert response.source == "local"


@pytest.mark.asyncio
async def test_fallback_merges_local_first_and_flags_source() -> None:
    local = FakeLocal([hit("https://example.com/a"), hit("https://example.com/b")], total=2)
    brave = FakeBrave(
        [hit("https://brave.example/1"), hit("https://brave.example/2"), hit("https://brave.example/3")],
        has_more=True,
    )

    response = await build_service(local, brave).search("privacy")

    assert brave.calls == [("privacy", 10, 0)]
    assert response.source == "brave"
    assert [item.url for item in response.results][:2] == ["https://example.com/a", "https://example.com/b"]
    assert [item.source for item in response.results] == ["local", "local", "brave", "brave", "brave"]
    assert response.pagination.total is None
    assert response.pagination.has_more is True
    assert response.meta.local_hits == 2
    assert response.meta.brave_hits == 3
    assert response.meta.degraded is False


@pytest.mark.asyncio
async def test_extra_snippets_are_exposed() -> None:
    local = FakeLocal()
    brave = FakeBrave(
        [hit("https://brave.example/1", extra_snippets=("Primo estratto", "Secondo estratto"))]
    )

    response = await build_service(local, brave).search("privacy")

    assert len(response.results) == 1
    assert response.results[0].extra_snippets == ["Primo estratto", "Secondo estratto"]


@pytest.mark.asyncio
async def test_local_highlights_are_exposed_as_segments() -> None:
    local = FakeLocal(
        [
            hit(
                "https://example.com/auto",
                "Auto usata",
                title_highlight=(
                    HighlightSpan("Auto", True),
                    HighlightSpan(" usata", False),
                ),
                snippet_highlight=(
                    HighlightSpan("Una ", False),
                    HighlightSpan("auto", True),
                ),
            )
        ]
    )

    response = await build_service(local, FakeBrave()).search("auto")

    result = response.results[0]
    assert [(segment.text, segment.match) for segment in result.title_highlight or []] == [
        ("Auto", True),
        (" usata", False),
    ]
    assert [(segment.text, segment.match) for segment in result.snippet_highlight or []] == [
        ("Una ", False),
        ("auto", True),
    ]


def test_merge_keeps_the_highlight_of_the_chosen_snippet_and_title() -> None:
    existing = RawHit(
        url="https://example.com/page",
        title="example.com",
        snippet="Auto",
        domain="example.com",
        title_highlight=(HighlightSpan("Auto", True),),
        snippet_highlight=(HighlightSpan("Auto", True),),
    )
    incoming = RawHit(
        url="https://example.com/page",
        title="Guida all'auto usata",
        snippet="Una guida completa all'auto usata",
        domain="example.com",
        title_highlight=(
            HighlightSpan("Guida all'", False),
            HighlightSpan("auto", True),
            HighlightSpan(" usata", False),
        ),
        snippet_highlight=(
            HighlightSpan("Una guida completa all'", False),
            HighlightSpan("auto", True),
            HighlightSpan(" usata", False),
        ),
    )

    merged = SearchService._enrich(existing, incoming)

    assert merged.title == "Guida all'auto usata"
    assert merged.snippet == "Una guida completa all'auto usata"
    assert merged.title_highlight == incoming.title_highlight
    assert merged.snippet_highlight == incoming.snippet_highlight


@pytest.mark.asyncio
async def test_duplicate_urls_are_merged() -> None:
    local = FakeLocal([hit("https://example.com/page")])
    brave = FakeBrave([hit("https://www.example.com/page/"), hit("https://other.example/x")])

    response = await build_service(local, brave).search("page")

    assert len(response.results) == 2
    assert [item.source for item in response.results] == ["local", "brave"]


@pytest.mark.asyncio
async def test_local_failure_is_partial_not_fatal() -> None:
    local = FakeLocal(error="meilisearch_connecterror")
    brave = FakeBrave([hit("https://brave.example/1")])

    response = await build_service(local, brave).search("privacy")

    assert response.source == "brave"
    assert response.meta.degraded is True
    assert response.meta.local_hits == 0
    assert response.pagination.total is None


@pytest.mark.asyncio
async def test_all_backends_failing_raises() -> None:
    local = FakeLocal(error="meilisearch_connecterror")
    brave = FakeBrave(error="brave_rate_limited")

    with pytest.raises(SearchUnavailable):
        await build_service(local, brave).search("privacy")


@pytest.mark.asyncio
async def test_no_brave_key_returns_empty_local_page() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/1")])

    response = await build_service(local, brave, brave_api_key="").search("privacy")

    assert brave.calls == []
    assert response.results == []
    assert response.source == "local"
    assert response.meta.degraded is False
    assert response.pagination.total == 0

    local = FakeLocal(error="meilisearch_connecterror")
    brave = FakeBrave()
    with pytest.raises(SearchUnavailable):
        await build_service(local, brave, brave_api_key="").search("privacy")


@pytest.mark.asyncio
async def test_pagination_offsets_local_and_brave() -> None:
    local = FakeLocal([hit(f"https://example.com/{i}") for i in range(20)], total=20)
    brave = FakeBrave()

    response = await build_service(local, brave, page_size=5).search("privacy", page=2, page_size=5)

    assert local.calls == [("privacy", 5, 5)]
    assert response.pagination.page == 2
    assert response.pagination.page_size == 5
    assert response.pagination.has_more is True
    assert response.pagination.total_pages == 4


@pytest.mark.asyncio
async def test_brave_failure_falls_through_to_next_provider() -> None:
    local = FakeLocal()
    brave = FakeBrave(error="brave_rate_limited")
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(local, brave, fallbacks=[exa]).search("privacy")

    assert brave.calls == [("privacy", 10, 0)]
    assert exa.calls == [("privacy", 10, 0)]
    assert response.source == "exa"
    assert response.results[0].source == "exa"
    assert response.meta.brave_hits == 0
    assert response.meta.external_hits == 1
    assert response.meta.degraded is True
    assert response.pagination.total is None


@pytest.mark.asyncio
async def test_empty_brave_page_falls_through_to_next_provider() -> None:
    local = FakeLocal()
    brave = FakeBrave([])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(local, brave, fallbacks=[exa]).search("privacy")

    assert exa.calls == [("privacy", 10, 0)]
    assert response.source == "exa"
    assert response.meta.degraded is False


@pytest.mark.asyncio
async def test_successful_brave_skips_the_rest_of_the_shield() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/1")])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(local, brave, fallbacks=[exa]).search("privacy")

    assert response.source == "brave"
    assert exa.calls == []


@pytest.mark.asyncio
async def test_shield_follows_configured_order_and_skips_disabled_providers() -> None:
    local = FakeLocal()
    brave = FakeBrave()
    disabled = FakeFallback("exa", [hit("https://exa.example/1")], configured=False)
    failing = FakeFallback("serper", error="serper_rate_limited")
    working = FakeFallback("tavily", [hit("https://tavily.example/1")])

    response = await build_service(
        local,
        brave,
        fallbacks=[disabled, failing, working],
        brave_api_key="",
    ).search("privacy")

    assert disabled.calls == []
    assert failing.calls == [("privacy", 10, 0)]
    assert working.calls == [("privacy", 10, 0)]
    assert response.source == "tavily"
    assert response.meta.degraded is True


@pytest.mark.asyncio
async def test_shield_ignores_provider_with_only_duplicate_urls() -> None:
    local = FakeLocal([hit("https://example.com/page")])
    brave = FakeBrave()
    duplicate = FakeFallback("exa", [hit("https://www.example.com/page/")])
    fresh = FakeFallback("serper", [hit("https://other.example/page")])

    response = await build_service(
        local,
        brave,
        fallbacks=[duplicate, fresh],
        brave_api_key="",
    ).search("page")

    assert duplicate.calls == [("page", 10, 0)]
    assert fresh.calls == [("page", 10, 0)]
    assert response.source == "serper"
    assert len(response.results) == 2


@pytest.mark.asyncio
async def test_all_shield_providers_failing_raises() -> None:
    local = FakeLocal(error="meilisearch_connecterror")
    brave = FakeBrave(error="brave_rate_limited")
    exa = FakeFallback("exa", error="exa_rate_limited")

    with pytest.raises(SearchUnavailable):
        await build_service(local, brave, fallbacks=[exa]).search("privacy")


@pytest.mark.asyncio
async def test_parallel_mode_calls_all_configured_providers_and_merges() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/1")])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])
    serper = FakeFallback("serper", [hit("https://serper.example/1")])

    response = await build_service(
        local,
        brave,
        fallbacks=[exa, serper],
        search_mode="parallel",
    ).search("privacy")

    assert brave.calls == [("privacy", 10, 0)]
    assert exa.calls == [("privacy", 10, 0)]
    assert serper.calls == [("privacy", 10, 0)]
    assert [item.source for item in response.results] == ["brave", "exa", "serper"]
    assert response.sources == ["brave", "exa", "serper"]
    assert response.source == "brave"
    assert response.meta.provider_hits == {"brave": 1, "exa": 1, "serper": 1}
    assert response.meta.external_hits == 3
    assert response.meta.degraded is False


@pytest.mark.asyncio
async def test_parallel_mode_respects_max_providers() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/1")])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])
    serper = FakeFallback("serper", [hit("https://serper.example/1")])

    response = await build_service(
        local,
        brave,
        fallbacks=[exa, serper],
        search_mode="parallel",
        search_max_providers=2,
    ).search("privacy")

    assert brave.calls == [("privacy", 10, 0)]
    assert exa.calls == [("privacy", 10, 0)]
    assert serper.calls == []
    assert response.sources == ["brave", "exa"]


@pytest.mark.asyncio
async def test_parallel_merge_enriches_duplicate_urls() -> None:
    local = FakeLocal()
    brave = FakeBrave(
        [hit("https://www.example.com/page/", extra_snippets=("Brave extra",))]
    )
    exa = FakeFallback(
        "exa",
        [hit("https://example.com/page", extra_snippets=("Exa extra",))],
    )

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="parallel",
    ).search("page")

    assert len(response.results) == 1
    assert response.results[0].source == "brave"
    assert response.results[0].extra_snippets == ["Brave extra", "Exa extra"]


@pytest.mark.asyncio
async def test_parallel_provider_failure_is_partial() -> None:
    local = FakeLocal()
    brave = FakeBrave(error="brave_rate_limited")
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="parallel",
    ).search("privacy")

    assert response.source == "exa"
    assert response.sources == ["exa"]
    assert response.meta.degraded is True
    assert response.meta.provider_hits == {"exa": 1}
    assert response.results[0].source == "exa"


@pytest.mark.asyncio
async def test_engine_multi_forces_parallel_even_with_fallback_setting() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/1")])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="fallback",
    ).search("privacy", engine="multi")

    assert brave.calls == [("privacy", 5, 0)]
    assert exa.calls == [("privacy", 5, 0)]
    assert response.engine == "multi"
    assert response.sources == ["brave", "exa"]
    assert response.available_providers == ["brave", "exa"]


@pytest.mark.asyncio
async def test_engine_multi_runs_external_even_with_enough_local_results() -> None:
    local = FakeLocal([hit(f"https://example.com/{i}") for i in range(3)])
    brave = FakeBrave([hit("https://brave.example/1")])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="fallback",
    ).search("privacy", engine="multi")

    assert brave.calls == [("privacy", 5, 0)]
    assert exa.calls == [("privacy", 5, 0)]
    assert response.engine == "multi"
    assert response.sources == ["local", "brave", "exa"]
    assert response.pagination.total is None


@pytest.mark.asyncio
async def test_engine_multi_leaves_room_for_external_results_on_a_full_local_page() -> None:
    local = FakeLocal([hit(f"https://example.com/{i}") for i in range(20)], total=20)
    brave = FakeBrave([hit(f"https://brave.example/{i}") for i in range(10)])
    exa = FakeFallback("exa", [hit(f"https://exa.example/{i}") for i in range(10)])

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="fallback",
    ).search("privacy", engine="multi")

    local_results = [item for item in response.results if item.source == "local"]
    external_results = [item for item in response.results if item.source != "local"]
    assert local.calls == [("privacy", 5, 0)]
    assert len(local_results) == 5
    assert external_results
    assert response.sources == ["local", "brave", "exa"]


@pytest.mark.asyncio
async def test_engine_multi_paginates_local_and_external_results_consistently() -> None:
    local = FakeLocal([hit(f"https://example.com/{i}") for i in range(20)], total=20)
    brave = FakeBrave([hit(f"https://brave.example/{i}") for i in range(20)])
    exa = FakeFallback("exa", [hit(f"https://exa.example/{i}") for i in range(20)])

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="fallback",
    ).search("privacy", page=2, engine="multi")

    assert local.calls == [("privacy", 5, 5)]
    assert brave.calls == [("privacy", 5, 1)]
    assert exa.calls == [("privacy", 5, 5)]
    assert len(response.results) == 10


@pytest.mark.asyncio
async def test_engine_provider_runs_even_with_enough_local_results() -> None:
    local = FakeLocal([hit(f"https://example.com/{i}") for i in range(3)])
    brave = FakeBrave([hit("https://brave.example/1")])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(local, brave, fallbacks=[exa]).search(
        "privacy",
        engine="brave",
    )

    assert brave.calls == [("privacy", 5, 0)]
    assert exa.calls == []
    assert response.engine == "brave"
    assert response.sources == ["local", "brave"]


@pytest.mark.asyncio
async def test_engine_provider_limits_the_shield_to_that_provider() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/1")])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(local, brave, fallbacks=[exa]).search(
        "privacy",
        engine="exa",
    )

    assert brave.calls == []
    assert exa.calls == [("privacy", 5, 0)]
    assert response.engine == "exa"
    assert response.source == "exa"
    assert response.sources == ["exa"]


@pytest.mark.asyncio
async def test_engine_local_skips_external_providers() -> None:
    local = FakeLocal([hit("https://example.com/local")])
    brave = FakeBrave([hit("https://brave.example/1")])

    response = await build_service(local, brave).search("privacy", engine="local")

    assert brave.calls == []
    assert response.engine == "local"
    assert response.source == "local"
    assert response.sources == ["local"]


@pytest.mark.asyncio
async def test_available_providers_excludes_missing_keys() -> None:
    local = FakeLocal()
    brave = FakeBrave()
    exa = FakeFallback("exa")

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        brave_api_key="",
    ).search("privacy")

    assert response.available_providers == ["exa"]


@pytest.mark.asyncio
async def test_crawled_at_is_exposed_for_local_results() -> None:
    local = FakeLocal([hit("https://example.com/a", crawled_at=1_700_000_000)])

    response = await build_service(local, FakeBrave()).search("privacy")

    assert response.results[0].crawled_at == 1_700_000_000


@pytest.mark.asyncio
async def test_local_results_are_reranked_by_freshness_at_equal_rank() -> None:
    now = int(time.time())
    local = FakeLocal(
        [
            hit("https://old.example/a", "Old page", domain="old.example", rank=0.9, crawled_at=now - 365 * 86_400),
            hit("https://new.example/b", "New page", domain="new.example", rank=0.9, crawled_at=now - 86_400),
        ]
    )

    response = await build_service(local, FakeBrave()).search("privacy")

    assert [item.url for item in response.results] == [
        "https://new.example/b",
        "https://old.example/a",
    ]


@pytest.mark.asyncio
async def test_rerank_keeps_local_results_before_external_ones() -> None:
    now = int(time.time())
    local = FakeLocal(
        [
            hit("https://old.example/a", "Old page", domain="old.example", rank=0.9, crawled_at=now - 365 * 86_400),
            hit("https://new.example/b", "New page", domain="new.example", rank=0.9, crawled_at=now),
        ],
        total=2,
    )
    brave = FakeBrave([hit("https://brave.example/1")])

    response = await build_service(local, brave).search("privacy")

    assert [item.source for item in response.results] == ["local", "local", "brave"]
    assert [item.url for item in response.results][:2] == [
        "https://new.example/b",
        "https://old.example/a",
    ]


@pytest.mark.asyncio
async def test_sort_crawled_at_desc_is_forwarded_and_keeps_index_order() -> None:
    now = int(time.time())
    local = FakeLocal(
        [
            hit("https://old.example/a", domain="old.example", rank=0.9, crawled_at=now - 365 * 86_400),
            hit("https://new.example/b", domain="new.example", rank=0.1, crawled_at=now),
        ]
    )

    response = await build_service(local, FakeBrave()).search("privacy", sort="crawled_at:desc")

    assert local.sorts == [["crawled_at:desc"]]
    assert [item.url for item in response.results] == [
        "https://old.example/a",
        "https://new.example/b",
    ]


@pytest.mark.asyncio
async def test_default_sort_sends_no_sort_expression() -> None:
    local = FakeLocal([hit("https://example.com/a")])

    await build_service(local, FakeBrave()).search("privacy")

    assert local.sorts == [None]


@pytest.mark.asyncio
async def test_parallel_insights_are_exposed() -> None:
    local = FakeLocal()
    brave = FakeBrave(
        insights=(RawInsight("brave", "Infobox", "Testo Brave", "https://brave.example/info"),)
    )
    exa = FakeFallback(
        "exa",
        [hit("https://exa.example/1")],
        insights=(RawInsight("exa", "Sintesi Exa", "Testo Exa"),),
    )

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="parallel",
    ).search("privacy")

    assert [insight.source for insight in response.meta.insights] == ["brave", "exa"]
    assert response.meta.insights[0].url == "https://brave.example/info"
    assert response.meta.insights[1].url is None


@pytest.mark.asyncio
async def test_filter_is_forwarded_to_the_local_index() -> None:
    local = FakeLocal([hit("https://example.com/a")])

    await build_service(local, FakeBrave()).search(
        "privacy", filter='domain = "example.com"'
    )

    assert local.filters == ['domain = "example.com"']


@pytest.mark.asyncio
async def test_default_search_sends_no_filter() -> None:
    local = FakeLocal([hit("https://example.com/a")])

    await build_service(local, FakeBrave()).search("privacy")

    assert local.filters == [None]


@pytest.mark.asyncio
async def test_excluded_terms_drop_local_hits_after_the_search() -> None:
    local = FakeLocal(
        [
            hit("https://example.com/spam", "Spam page"),
            hit("https://example.com/ok", "Good page"),
        ]
    )

    response = await build_service(local, FakeBrave()).search("page", excluded=("spam",))

    assert local.calls == [("page", 10, 0)]
    assert [item.url for item in response.results] == ["https://example.com/ok"]
    assert response.meta.local_hits == 1


@pytest.mark.asyncio
async def test_excluded_terms_do_not_touch_external_results() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/spam", "Spam result")])

    response = await build_service(local, brave).search("page", excluded=("spam",))

    assert [item.source for item in response.results] == ["brave"]


@pytest.mark.asyncio
async def test_suggest_returns_local_prefix_matches() -> None:
    local = FakeLocal(suggestions=[hit("https://example.com/auto", "Auto usata")])

    hits = await build_service(local, FakeBrave()).suggest("au")

    assert local.suggest_calls == [("au", 6)]
    assert [hit.title for hit in hits] == ["Auto usata"]


@pytest.mark.asyncio
async def test_suggest_degrades_to_an_empty_list() -> None:
    local = FakeLocal(error="meilisearch_connecterror")

    assert await build_service(local, FakeBrave()).suggest("au") == []


@pytest.mark.asyncio
async def test_empty_index_is_flagged_only_for_empty_responses() -> None:
    empty = FakeLocal(index_empty=True)
    response = await build_service(empty, FakeBrave(), brave_api_key="").search("privacy")

    assert response.results == []
    assert response.meta.local_index_empty is True
    assert empty.index_empty_calls == 1

    populated = FakeLocal([hit("https://example.com/a")], index_empty=False)
    filled = await build_service(populated, FakeBrave()).search("privacy")

    assert filled.meta.local_index_empty is False
    assert populated.index_empty_calls == 0

    no_match = FakeLocal(index_empty=False)
    missing = await build_service(no_match, FakeBrave(), brave_api_key="").search("privacy")

    assert missing.meta.local_index_empty is False
    assert no_match.index_empty_calls == 1


@pytest.mark.asyncio
async def test_empty_page_suggests_the_closest_index_spelling() -> None:
    local = FakeLocal(index_empty=False, corrections=["Intelligenza"])

    response = await build_service(local, FakeBrave(), brave_api_key="").search(
        "inteliggenza"
    )

    assert response.results == []
    assert response.meta.query_suggestion == "Intelligenza"
    assert local.correction_calls == ["inteliggenza"]


@pytest.mark.asyncio
async def test_query_suggestion_corrects_every_word_of_a_short_query() -> None:
    local = FakeLocal(index_empty=False, corrections=["Intelligenza", "Artificiale"])

    response = await build_service(local, FakeBrave(), brave_api_key="").search(
        "inteliggenza artificale"
    )

    assert response.meta.query_suggestion == "Intelligenza Artificiale"


@pytest.mark.asyncio
async def test_exact_spelling_is_not_proposed_as_a_correction() -> None:
    local = FakeLocal(index_empty=False, corrections=["inteliggenza"])

    response = await build_service(local, FakeBrave(), brave_api_key="").search(
        "inteliggenza"
    )

    assert response.meta.query_suggestion is None


@pytest.mark.asyncio
async def test_no_correction_lookup_on_a_non_empty_page_or_an_empty_index() -> None:
    populated = FakeLocal([hit("https://example.com/a")], corrections=["Intelligenza"])
    await build_service(populated, FakeBrave()).search("inteliggenza")

    assert populated.correction_calls == []

    empty_index = FakeLocal(index_empty=True, corrections=["Intelligenza"])
    response = await build_service(empty_index, FakeBrave(), brave_api_key="").search(
        "inteliggenza"
    )

    assert response.meta.query_suggestion is None
    assert empty_index.correction_calls == []


@pytest.mark.asyncio
async def test_quoted_phrases_are_never_rewritten() -> None:
    local = FakeLocal(index_empty=False, corrections=["Intelligenza"])

    response = await build_service(local, FakeBrave(), brave_api_key="").search(
        '"inteliggenza"'
    )

    assert response.meta.query_suggestion is None
    assert local.correction_calls == []


@pytest.mark.asyncio
async def test_identical_external_search_reuses_the_cached_response() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/1")])
    service = build_service(local, brave)

    first = await service.search("privacy")
    second = await service.search("  Privacy  ")

    assert len(brave.calls) == 1
    assert [item.url for item in second.results] == [item.url for item in first.results]
    assert second.meta.provider_hits == {"brave": 1}


@pytest.mark.asyncio
async def test_cache_is_keyed_by_page() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/1")])
    service = build_service(local, brave)

    await service.search("privacy")
    await service.search("privacy", page=2)

    assert brave.calls == [("privacy", 10, 0), ("privacy", 10, 1)]


@pytest.mark.asyncio
async def test_cache_does_not_store_provider_failures() -> None:
    local = FakeLocal()
    brave = FakeBrave(error="brave_rate_limited")
    exa = FakeFallback("exa", [hit("https://exa.example/1")])
    service = build_service(local, brave, fallbacks=[exa])

    await service.search("privacy")
    await service.search("privacy")

    # The working provider is cached after the first call, the failing one is
    # retried: a failure must never be replayed from the cache.
    assert len(brave.calls) == 2
    assert len(exa.calls) == 1


@pytest.mark.asyncio
async def test_open_circuit_skips_a_repeatedly_failing_provider() -> None:
    local = FakeLocal()
    brave = FakeBrave(error="brave_rate_limited")
    exa = FakeFallback("exa", [hit("https://exa.example/1")])
    service = build_service(local, brave, fallbacks=[exa])

    for _ in range(3):
        await service.search("privacy")

    response = await service.search("privacy")

    assert len(brave.calls) == 3
    assert response.meta.provider_errors == ["brave"]
    assert response.meta.provider_hits == {"exa": 1}
    assert response.meta.degraded is True


@pytest.mark.asyncio
async def test_provider_errors_name_only_the_failed_providers() -> None:
    local = FakeLocal()
    brave = FakeBrave(error="brave_rate_limited")
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(local, brave, fallbacks=[exa]).search("privacy")

    assert response.meta.provider_errors == ["brave"]
    assert response.meta.provider_hits == {"exa": 1}
    assert response.meta.degraded is True


@pytest.mark.asyncio
async def test_parallel_merge_weighs_provider_and_position() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit(f"https://brave.example/{index}") for index in range(3)])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="parallel",
    ).search("privacy")

    urls = [item.url for item in response.results]
    assert urls[0] == "https://brave.example/0"
    # Exa's first hit outranks Brave's third one: the merge is not plain
    # round-robin, a deep hit does not beat a provider's top result.
    assert urls.index("https://exa.example/1") < urls.index("https://brave.example/2")
    assert urls[-1] == "https://brave.example/2"


@pytest.mark.asyncio
async def test_fallback_timeout_moves_to_the_next_provider() -> None:
    local = FakeLocal()
    timing_out = TimeoutFallback("exa", [hit("https://exa.example/1")])
    working = FakeFallback("serper", [hit("https://serper.example/1")])

    response = await build_service(
        local,
        FakeBrave(),
        fallbacks=[timing_out, working],
        brave_api_key="",
    ).search("privacy")

    assert working.calls == [("privacy", 10, 0)]
    assert response.source == "serper"
    assert response.meta.degraded is True
    assert response.meta.provider_errors == ["exa"]


@pytest.mark.asyncio
async def test_exhausted_budget_skips_the_remaining_providers() -> None:
    local = FakeLocal([hit("https://example.com/a")])
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(
        local,
        FakeBrave(),
        fallbacks=[exa],
        brave_api_key="",
        external_budget_s=0.001,
    ).search("privacy", engine="exa")

    assert exa.calls == []
    assert response.meta.degraded is True
    assert response.results[0].source == "local"


@pytest.mark.asyncio
async def test_unexpected_provider_exception_is_contained() -> None:
    local = FakeLocal()
    crashing = CrashFallback("exa", [hit("https://exa.example/1")])
    working = FakeFallback("serper", [hit("https://serper.example/1")])

    response = await build_service(
        local,
        FakeBrave(),
        fallbacks=[crashing, working],
        brave_api_key="",
    ).search("privacy")

    assert response.source == "serper"
    assert response.meta.degraded is True
    assert response.meta.provider_errors == ["exa"]


@pytest.mark.asyncio
async def test_parallel_mode_times_out_slow_providers() -> None:
    local = FakeLocal([hit("https://example.com/a")])
    exa = SlowFallback("exa", [hit("https://exa.example/1")])

    response = await build_service(
        local,
        SlowBrave([hit("https://brave.example/1")]),
        fallbacks=[exa],
        search_mode="parallel",
        external_budget_s=0.001,
    ).search("privacy", engine="multi")

    assert response.results[0].source == "local"
    assert response.meta.degraded is True
    assert set(response.meta.provider_errors) == {"brave", "exa"}
    assert response.meta.provider_hits == {}


@pytest.mark.asyncio
async def test_spelling_lookup_failure_only_removes_the_suggestion() -> None:
    local = BrokenWordsLocal(index_empty=False)

    response = await build_service(local, FakeBrave(), brave_api_key="").search(
        "inteliggenza"
    )

    assert response.results == []
    assert response.meta.query_suggestion is None


@pytest.mark.asyncio
async def test_short_words_are_not_looked_up_for_a_correction() -> None:
    local = FakeLocal(index_empty=False, corrections=["casa"])

    response = await build_service(local, FakeBrave(), brave_api_key="").search("caas")

    assert response.meta.query_suggestion is None
    assert local.correction_calls == []


@pytest.mark.asyncio
async def test_local_availability_and_provider_status() -> None:
    service = build_service(
        FakeLocal(),
        FakeBrave(),
        fallbacks=[FakeFallback("exa", configured=False)],
    )

    assert await service.local_available() is True
    assert service.provider_status() == {"brave": "configured", "exa": "disabled"}


@pytest.mark.asyncio
async def test_parallel_mode_skips_a_provider_with_an_open_circuit() -> None:
    local = FakeLocal()
    brave = FakeBrave(error="brave_rate_limited")
    exa = FakeFallback("exa", [hit("https://exa.example/1")])
    service = build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="parallel",
        provider_failure_threshold=1,
    )

    await service.search("privacy")
    response = await service.search("privacy")

    # After the first failure the breaker opens: the second search reports the
    # provider as unavailable without calling it again.
    assert response.meta.provider_errors == ["brave"]
    assert response.meta.provider_hits == {"exa": 1}
    assert response.source == "exa"


@pytest.mark.asyncio
async def test_disabling_the_cache_calls_the_provider_every_time() -> None:
    local = FakeLocal()
    brave = FakeBrave([hit("https://brave.example/1")])
    service = build_service(local, brave, provider_cache_ttl_s=0)

    await service.search("privacy")
    await service.search("privacy")

    assert len(brave.calls) == 2


@pytest.mark.asyncio
async def test_insights_are_capped_and_deduplicated() -> None:
    local = FakeLocal()
    shared = tuple(
        RawInsight("brave", f"Infobox {index}", f"Testo {index}") for index in range(4)
    )
    brave = FakeBrave([hit("https://brave.example/1")], insights=shared)
    exa = FakeFallback(
        "exa",
        [hit("https://exa.example/1")],
        insights=tuple(
            RawInsight("exa", f"Infobox {index}", f"Testo {index}")
            for index in range(4, 9)
        ),
    )

    response = await build_service(
        local,
        brave,
        fallbacks=[exa],
        search_mode="parallel",
    ).search("privacy")

    assert len(response.meta.insights) == 6
    keys = [(insight.url, insight.text) for insight in response.meta.insights]
    assert len(keys) == len(set(keys))
