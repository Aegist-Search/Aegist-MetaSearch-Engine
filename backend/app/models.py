"""Pydantic schemas exposed by the public API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ResultSource = Literal["local", "brave", "exa", "serper", "tavily"]
SearchEngine = Literal["auto", "multi", "local", "brave", "exa", "serper", "tavily"]
SearchSort = Literal["relevance", "crawled_at:desc"]


class HighlightSegment(BaseModel):
    """One run of a title/snippet, split around the query matches."""

    text: str
    match: bool = Field(
        default=False,
        description="True when this run was matched by the query and should be highlighted",
    )


class SearchResultItem(BaseModel):
    id: str = Field(description="Stable identifier derived from the result URL")
    title: str
    url: str
    snippet: str = ""
    title_highlight: list[HighlightSegment] | None = Field(
        default=None,
        description=(
            "Title split into plain and matched runs (local results only). "
            "Render it as text: it does not contain HTML."
        ),
    )
    snippet_highlight: list[HighlightSegment] | None = Field(
        default=None,
        description=(
            "Snippet split into plain and matched runs (local results only). "
            "Render it as text: it does not contain HTML."
        ),
    )
    extra_snippets: list[str] = Field(
        default_factory=list,
        description="Additional excerpts returned by the external provider",
    )
    source: ResultSource
    domain: str
    h1: str | None = None
    rank: float | None = Field(default=None, description="Meilisearch relevance score (local results only)")
    image: str | None = Field(
        default=None,
        description=(
            "Absolute URL of the page preview image (og:image) discovered while "
            "crawling. Load it through /api/v1/preview: the backend fetches and "
            "caches it, the browser never contacts the origin site."
        ),
    )
    crawled_at: int | None = Field(
        default=None,
        description=(
            "Unix timestamp (seconds) of the crawl that indexed this page "
            "(local results only). Used for the freshness ranking and shown as "
            "an \"updated on\" label in the interface."
        ),
    )


class SearchInsight(BaseModel):
    source: ResultSource
    title: str
    text: str
    url: str | None = None


class SearchPagination(BaseModel):
    page: int
    page_size: int
    has_more: bool
    total: int | None = Field(default=None, description="Only available when the page is served locally")
    total_pages: int | None = None


class SearchMeta(BaseModel):
    took_ms: int
    local_hits: int
    brave_hits: int
    external_hits: int = Field(
        default=0,
        description="Unique external results that contributed to the page",
    )
    provider_hits: dict[str, int] = Field(
        default_factory=dict,
        description="Raw result count returned by each external provider that responded",
    )
    provider_errors: list[str] = Field(
        default_factory=list,
        description=(
            "External providers that failed or were skipped after repeated "
            "failures in this search. Names only: no error detail, no query."
        ),
    )
    local_index_empty: bool = Field(
        default=False,
        description=(
            "True when the response is empty and the local index holds no "
            "documents at all (a fresh install). Only checked for empty pages."
        ),
    )
    query_suggestion: str | None = Field(
        default=None,
        description=(
            "Corrected query when an empty page looks like a typo the local "
            "index can resolve. It is computed on the fly and never stored."
        ),
    )
    insights: list[SearchInsight] = Field(
        default_factory=list,
        description="Provider-specific answers/knowledge snippets for the query",
    )
    degraded: bool = Field(default=False, description="True when a backend failed and the answer is partial")
    crawl_scheduled: bool = Field(
        default=False,
        description=(
            "True when this search scheduled a background Katana crawl: the "
            "results may improve on a later search for the same page/site."
        ),
    )


class SearchResponse(BaseModel):
    query: str
    engine: SearchEngine = Field(
        default="auto",
        description="Requested search engine selection",
    )
    source: ResultSource
    sources: list[ResultSource] = Field(
        default_factory=list,
        description="All sources that contributed to the page, in priority order",
    )
    available_providers: list[str] = Field(
        default_factory=list,
        description="External providers with a configured API key",
    )
    results: list[SearchResultItem]
    pagination: SearchPagination
    meta: SearchMeta


class SuggestionItem(BaseModel):
    """One autocomplete entry, taken from the local index only."""

    title: str
    url: str
    domain: str = Field(description="Hostname of the page, without www.")


class SuggestResponse(BaseModel):
    query: str = Field(description="Query used for the prefix search, operators removed")
    suggestions: list[SuggestionItem] = Field(default_factory=list)


class ProxyStatus(BaseModel):
    """Aggregate SOCKS5 pool state: counters and reason codes only."""

    enabled: bool
    pool: int = Field(description="Candidates currently held in the pool")
    available: int = Field(description="Candidates not in cooldown")
    quarantined: int = Field(description="Candidates in cooldown after a failure")
    last_refresh_s: float | None = Field(
        default=None,
        description="Seconds since the last successful refresh",
    )
    last_error: str | None = Field(
        default=None,
        description="Last list/validation reason code, never a proxy address",
    )


class CrawlStatus(BaseModel):
    """On-demand crawl state: counters and reason codes only."""

    enabled: bool
    binary: bool = Field(description="True when the katana executable is available")
    running: int = Field(description="Crawls reserved or running right now")
    reserved: int = Field(description="Domains currently reserved by a crawl")
    throttled: int = Field(default=0, description="Crawl starts skipped by the rate limiter")
    last_reason: str | None = Field(
        default=None,
        description="Last outcome code (never a URL or a domain)",
    )


class TorStatus(BaseModel):
    """Result of the Tor exit-node probe (no user data is involved)."""

    tor_active: bool
    exit_ip: str | None = Field(
        default=None,
        description="Address of the Tor exit node used by the server, when known",
    )
    latency_ms: int | None = Field(
        default=None,
        description="Round-trip time of the probe through the Tor proxy",
    )
    error: str | None = Field(
        default=None,
        description="Reason code when Tor is not active (tor_timeout, tor_unreachable, ...)",
    )


class MetricsResponse(BaseModel):
    """Aggregate counters, in process memory only.

    No query, URL or address is ever part of this payload: the fields are
    totals and provider names, so the endpoint can be exposed for monitoring
    without weakening the privacy guarantees of the engine.
    """

    uptime_s: int = Field(description="Seconds since the process started")
    searches_total: int = Field(description="Searches answered, including failed ones")
    searches_failed_total: int = Field(description="Searches where no backend answered (HTTP 503)")
    searches_degraded_total: int = Field(description="Answers served without a backend that failed")
    searches_empty_total: int = Field(description="Searches that produced no result for the page")
    local_hits_total: int = Field(description="Local index hits returned across all searches")
    external_hits_total: int = Field(description="External results that reached a page")
    provider_errors_total: dict[str, int] = Field(
        default_factory=dict,
        description="Per-provider count of searches where that provider failed or was skipped",
    )


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    meilisearch: Literal["up", "down"]
    brave: Literal["configured", "disabled"]
    fallbacks: dict[str, str] = Field(
        default_factory=dict,
        description="Configured/disabled state of optional external providers (no keys)",
    )
    index: str
    version: str
    proxy: ProxyStatus | None = Field(
        default=None,
        description="SOCKS5 rotation pool state (counters and reason codes only)",
    )
    crawl: CrawlStatus | None = Field(
        default=None,
        description="On-demand Katana crawl state (counters and reason codes only)",
    )
