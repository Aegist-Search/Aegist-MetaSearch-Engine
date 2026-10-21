"""Search endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from app.models import SearchEngine, SearchResponse, SearchSort
from app.services.crawler_service import CrawlerService
from app.services.search import SearchService, SearchUnavailable
from app.utils.query import build_filter, parse_query
from app.utils.urls import normalize_url

router = APIRouter(tags=["search"])


def _crawl_seed(query: str, response: SearchResponse) -> str | None:
    """URL to crawl for this search, or ``None`` when a crawl makes no sense.

    An explicit http(s) query is always the seed: it is the page the user
    wants indexed. A free-text query is only a hint, so it becomes a seed only
    when the local index had nothing *and* the external shield found a page;
    crawling an arbitrary search term would be meaningless.
    """

    explicit = normalize_url(query)
    if explicit:
        return explicit
    if response.meta.local_hits == 0 and response.results:
        return response.results[0].url
    return None


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Search the web",
    description=(
        "Queries the local Meilisearch index first. With engine=auto, the "
        "configured external providers (Brave, Exa, Serper, Tavily) fill the gap "
        "when fewer than three local results are available, sequentially or in "
        "parallel depending on SEARCH_MODE. An explicit engine runs the "
        "external shield even with enough local results: engine=multi forces the "
        "parallel fan-out, a provider name limits the search to that provider, "
        "and engine=local skips external providers entirely. Provider-specific "
        "insights are returned with the results. Local results are reranked with "
        "a combined score (Meilisearch relevance, title match, freshness and "
        "domain variety); sort=crawled_at:desc requests the plain chronological "
        "order instead. The query accepts site:example.com, \"exact phrase\" "
        "and -exclude operators, and the domain/lang parameters restrict the "
        "local index further; all of them apply to local results only. When "
        "the local index has no "
        "result, or when the query is an http(s) URL, a Katana crawl is queued "
        "on FastAPI BackgroundTasks and indexed for later searches: the "
        "response is never delayed by it and reports meta.crawl_scheduled. "
        "Queries are never logged or stored."
    ),
)
async def search(
    request: Request,
    background: BackgroundTasks,
    q: Annotated[str, Query(min_length=1, max_length=400, description="Search query")],
    page: Annotated[int, Query(ge=1, le=10, description="1-based result page")] = 1,
    page_size: Annotated[int | None, Query(ge=1, le=20, description="Results per page")] = None,
    engine: Annotated[
        SearchEngine,
        Query(description="auto, multi, local, or a specific provider"),
    ] = "auto",
    sort: Annotated[
        SearchSort,
        Query(
            description=(
                "relevance (default: combined local score) or crawled_at:desc "
                "(newest pages first, no rerank)"
            )
        ),
    ] = "relevance",
    domain: Annotated[
        str | None,
        Query(
            max_length=253,
            pattern=(
                r"(?i)^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$"
            ),
            description="Restrict the local index to one domain (e.g. example.com)",
        ),
    ] = None,
    lang: Annotated[
        str | None,
        Query(
            max_length=3,
            pattern=r"^[a-z]{2,3}$",
            description="Restrict the local index to one language code (e.g. it)",
        ),
    ] = None,
) -> SearchResponse:
    parsed = parse_query(" ".join(q.split()))
    if not parsed.text:
        raise HTTPException(status_code=422, detail="Query must not be empty.")

    filters = build_filter(
        sites=parsed.sites,
        domains=(domain.casefold().strip("."),) if domain else (),
        langs=(lang,) if lang else (),
    )

    service: SearchService = request.app.state.search_service
    try:
        response = await service.search(
            parsed.text,
            page=page,
            page_size=page_size,
            engine=engine,
            sort=sort,
            filter=filters,
            excluded=parsed.excluded,
        )
    except SearchUnavailable:
        # Deliberately generic: no backend detail and no query echoed back.
        raise HTTPException(status_code=503, detail="Search backends are temporarily unavailable.") from None

    crawl_scheduled = await _schedule_crawl(
        request, background, parsed.text, response, page, engine
    )
    if crawl_scheduled:
        response = response.model_copy(
            update={"meta": response.meta.model_copy(update={"crawl_scheduled": True})}
        )
    return response


async def _schedule_crawl(
    request: Request,
    background: BackgroundTasks,
    query: str,
    response: SearchResponse,
    page: int,
    engine: SearchEngine,
) -> bool:
    """Queue the background crawl, if the service accepts it.

    Only the first page is a seed: paginating through results must not start a
    crawl per page. ``engine=local`` is an explicit "do not touch the network"
    request, so it never crawls either.
    """

    crawler: CrawlerService | None = getattr(request.app.state, "crawler", None)
    if crawler is None or page != 1 or engine == "local":
        return False

    seed = _crawl_seed(query, response)
    if seed is None:
        return False

    target = await crawler.schedule(seed)
    if target is None:
        return False

    # The request returns immediately; the crawl runs after the response.
    background.add_task(crawler.run, target)
    return True
