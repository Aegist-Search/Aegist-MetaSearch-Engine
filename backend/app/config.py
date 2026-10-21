"""Typed runtime configuration.

Every setting can be overridden through an environment variable with the same
name in upper case (``MEILI_URL``, ``BRAVE_API_KEY``, ...). A local ``.env``
file is read when present.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Aegis Search API"
    environment: str = "development"

    # --- Local index ---------------------------------------------------------
    meili_url: str = "http://127.0.0.1:7700"
    meili_master_key: str = ""
    meili_index: str = "pages"
    meili_timeout_s: float = Field(default=4.0, gt=0)

    # --- Brave fallback ------------------------------------------------------
    brave_enabled: bool = True
    brave_api_key: str = ""
    brave_base_url: str = "https://api.search.brave.com/res/v1"
    brave_timeout_s: float = Field(default=7.0, gt=0)
    brave_safesearch: str = "moderate"
    brave_country: str = ""
    brave_search_lang: str = ""
    brave_ui_lang: str = ""
    brave_extra_snippets: bool = True

    # --- Optional external providers (fallback shield) ----------------------
    # Order in which providers are tried after Brave when the local page is
    # below ``MIN_LOCAL_RESULTS``. A provider without an API key is skipped.
    search_fallback_order: str = "exa,serper,tavily"
    # ``fallback`` keeps costs low by calling one provider at a time.
    # ``parallel`` queries up to SEARCH_MAX_PROVIDERS together and merges
    # their results, at the cost of more API calls per search.
    search_mode: str = "fallback"
    search_max_providers: int = Field(default=4, ge=1, le=4)

    exa_enabled: bool = True
    exa_api_key: str = ""
    exa_timeout_s: float = Field(default=7.0, gt=0)
    exa_search_type: str = "auto"
    exa_moderation: bool = True
    # Adds Exa summaries to the response as provider insights. Costs extra.
    exa_include_summary: bool = False

    serper_enabled: bool = True
    serper_api_key: str = ""
    serper_base_url: str = "https://google.serper.dev"
    serper_timeout_s: float = Field(default=7.0, gt=0)
    serper_gl: str = ""
    serper_hl: str = ""

    tavily_enabled: bool = True
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    tavily_timeout_s: float = Field(default=7.0, gt=0)
    tavily_search_depth: str = "basic"
    tavily_topic: str = "general"
    tavily_language: str = ""
    tavily_safe_search: bool = True
    # Tavily answer/raw content add cost; enable them only when useful.
    tavily_include_answer: bool = False
    tavily_include_raw_content: bool = False

    user_agent: str = "AegisSearch/1.0"

    # --- SOCKS5 proxy rotation ------------------------------------------------
    # Every outbound call to the external search providers can be routed
    # through a rotating SOCKS5 proxy. The local index (Meilisearch) and the
    # preview downloader deliberately stay direct.
    proxy_enabled: bool = True
    proxy_list_url: str = (
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"
    )
    # Extra proxies merged into the fetched list, one per line; accepts
    # ``host:port``, ``user:pass@host:port`` and ``socks5://...`` forms.
    proxy_list_extra: str = ""
    proxy_refresh_interval_s: float = Field(default=900.0, ge=30)
    # When the pool is empty the background task retries sooner.
    proxy_retry_interval_s: float = Field(default=60.0, ge=5)
    proxy_fetch_timeout_s: float = Field(default=10.0, gt=0)
    # Upper bound for fetch + validation during application startup. The
    # refresh keeps running in the background when the budget is exceeded.
    proxy_startup_timeout_s: float = Field(default=8.0, gt=0)
    # Hard cap on the number of candidates kept from the public list.
    proxy_pool_size: int = Field(default=50, ge=1, le=500)
    proxy_validate_on_load: bool = True
    proxy_validation_timeout_s: float = Field(default=2.5, gt=0)
    proxy_validation_concurrency: int = Field(default=20, ge=1, le=100)
    # Optional HTTP probe executed through each proxy. Empty means the lighter
    # SOCKS5 handshake probe is used (no third-party request at all).
    proxy_validate_url: str = ""
    # Attempts per outbound call, each with a different proxy.
    proxy_max_attempts: int = Field(default=3, ge=1, le=5)
    # Keep attempts x connect timeout below EXTERNAL_BUDGET_S so the direct
    # fallback still runs when a whole batch of free proxies is dead.
    proxy_connect_timeout_s: float = Field(default=2.0, gt=0)
    proxy_cooldown_s: float = Field(default=120.0, gt=0)
    proxy_max_cooldown_s: float = Field(default=900.0, gt=0)
    # Maximum number of live httpx clients kept in the per-proxy pool.
    # Keep it >= PROXY_POOL_SIZE to avoid closing a client that a concurrent
    # request is still using.
    proxy_max_clients: int = Field(default=64, ge=1, le=500)
    # Direct connection is the last resort when no proxy answers. When false,
    # the provider call fails with its usual reason code instead.
    proxy_fallback_direct: bool = True
    # Minimum interval between "using direct connection" warnings.
    proxy_warning_interval_s: float = Field(default=60.0, ge=0)

    # --- On-demand crawl (Katana) --------------------------------------------
    # When a search has no local result, or when the query is an http(s) URL,
    # the API schedules a Katana crawl of that site in the background and
    # indexes it for future searches. The HTTP response never waits for it.
    crawl_enabled: bool = True
    # Executable name (resolved with PATH) or absolute path.
    katana_binary: str = "katana"
    # Directory shared with the backend container. Each run writes its own
    # JSONL file so two crawls can never interleave in the same file.
    crawl_output_dir: str = "/data"
    crawl_output_prefix: str = "dynamic_crawl"
    # Keep the JSONL files for debugging; they are deleted after ingestion
    # otherwise, since the documents already live in Meilisearch.
    crawl_keep_outputs: bool = False
    # Katana -d. Keep it low: the crawl runs on the server, in background.
    crawl_depth: int = Field(default=1, ge=0, le=5)
    # Katana -rl: requests per second, a politeness limit towards the site.
    crawl_rate_limit: int = Field(default=20, ge=1, le=500)
    # Token bucket on crawl *starts*: protects the server when the API is
    # reachable by untrusted clients. Excess crawls are skipped, never queued.
    # The bucket is global (one counter), so it stores no IP and no domain.
    crawl_max_starts_per_minute: float = Field(default=6.0, gt=0, le=120)
    crawl_start_burst: int = Field(default=2, ge=1, le=20)
    # Katana -timeout: seconds per HTTP request.
    crawl_request_timeout_s: int = Field(default=10, ge=1, le=120)
    # Hard wall-clock limit for the whole Katana process.
    crawl_timeout_s: float = Field(default=90.0, gt=0, le=600)
    # The public-address check runs in the background, before spawning Katana:
    # this timeout bounds a slow or hanging resolver.
    crawl_dns_timeout_s: float = Field(default=3.0, gt=0, le=10)
    # Hard limit for the ingestion step (Meilisearch upload + task polling).
    crawl_ingest_timeout_s: float = Field(default=60.0, gt=0, le=300)
    # Crawls reserved/running at the same time, process-wide.
    crawl_max_concurrency: int = Field(default=2, ge=1, le=10)
    # A domain is not crawled again for this long after a successful run.
    crawl_domain_cooldown_s: float = Field(default=900.0, ge=0)
    # Longer pause after a failure, so a broken site cannot retry forever.
    crawl_failure_cooldown_s: float = Field(default=1800.0, ge=0)
    crawl_ingest_batch_size: int = Field(default=200, ge=1, le=1000)

    # --- Ingestion -----------------------------------------------------------
    # Near-duplicate filter applied by the CLI and by the on-demand crawler: a
    # pagination/print variant of a page would otherwise take a slot of its own.
    # DEDUPE_THRESHOLD is a SimHash similarity in (0, 1]: 1.0 drops only
    # identical content, lower values also merge lightly edited copies.
    dedupe_enabled: bool = True
    dedupe_threshold: float = Field(default=0.9, gt=0, le=1)
    dedupe_prefix_chars: int = Field(default=2000, ge=200, le=20_000)

    # --- Tor diagnostics ------------------------------------------------------
    # Checked by /api/v1/tor-status and backend/scripts/test_tor.py. This is
    # independent from the external-provider proxy rotation: it only answers
    # "is this SOCKS5 endpoint a working Tor exit?".
    tor_enabled: bool = True
    tor_proxy_url: str = "socks5://127.0.0.1:9050"
    tor_status_url: str = "https://check.torproject.org/api/ip"
    tor_timeout_s: float = Field(default=5.0, gt=0, le=30)
    # The frontend polls the endpoint: cache the answer instead of hitting the
    # check service (and the Tor circuit) on every request.
    tor_cache_ttl_s: float = Field(default=30.0, ge=0, le=600)

    # --- Search behaviour ----------------------------------------------------
    min_local_results: int = Field(default=3, ge=0, le=20)
    page_size: int = Field(default=10, ge=1, le=20)
    max_page: int = Field(default=10, ge=1, le=10)
    # Hard cap for the external provider phase (fallback or parallel). Keeps
    # the request under the frontend timeout even when several providers fail.
    external_budget_s: float = Field(default=8.0, gt=0, le=30)
    # A repeated provider request within this TTL reuses the response kept in
    # process memory (no disk, no logs, no extra quota). 0 disables the cache.
    provider_cache_ttl_s: float = Field(default=180.0, ge=0, le=3600)
    # Upper bound on cached responses, so a long-running process stays bounded.
    provider_cache_max_entries: int = Field(default=256, ge=1, le=4096)
    # After N consecutive errors a provider is skipped for
    # PROVIDER_COOLDOWN_S instead of costing the timeout on every search.
    provider_failure_threshold: int = Field(default=3, ge=1, le=10)
    provider_cooldown_s: float = Field(default=60.0, gt=0, le=600)

    # --- Request rate limiting -----------------------------------------------
    # One in-memory token bucket per salted hash of the client address. The
    # raw address is never kept, logged or returned; the salt is random per
    # process, so the digest cannot be matched across restarts. Behind the
    # Next.js server all requests share its address: there the bucket is
    # effectively global, which still protects provider quotas and the crawl.
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: float = Field(default=120.0, gt=0, le=6000)
    rate_limit_burst: int = Field(default=30, ge=1, le=1000)
    rate_limit_max_clients: int = Field(default=4096, ge=1, le=100_000)

    # --- HTTP ----------------------------------------------------------------
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    request_timeout_s: float = Field(default=12.0, gt=0)

    @field_validator("brave_safesearch")
    @classmethod
    def _normalise_safesearch(cls, value: str) -> str:
        normalised = value.strip().lower()
        return normalised if normalised in {"off", "moderate", "strict"} else "moderate"

    @field_validator("search_mode")
    @classmethod
    def _normalise_search_mode(cls, value: str) -> str:
        normalised = value.strip().lower()
        return normalised if normalised in {"fallback", "parallel"} else "fallback"

    @field_validator("exa_search_type")
    @classmethod
    def _normalise_exa_search_type(cls, value: str) -> str:
        allowed = {
            "auto",
            "fast",
            "instant",
            "deep-lite",
            "deep",
            "deep-reasoning",
            "neural",
        }
        normalised = value.strip().lower()
        return normalised if normalised in allowed else "auto"

    @field_validator("tavily_search_depth")
    @classmethod
    def _normalise_tavily_depth(cls, value: str) -> str:
        normalised = value.strip().lower()
        return normalised if normalised in {"basic", "advanced"} else "basic"

    @field_validator("tavily_topic")
    @classmethod
    def _normalise_tavily_topic(cls, value: str) -> str:
        normalised = value.strip().lower()
        return normalised if normalised in {"general", "news"} else "general"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def brave_configured(self) -> bool:
        return self.brave_enabled and bool(self.brave_api_key.strip())

    @property
    def exa_configured(self) -> bool:
        return self.exa_enabled and bool(self.exa_api_key.strip())

    @property
    def serper_configured(self) -> bool:
        return self.serper_enabled and bool(self.serper_api_key.strip())

    @property
    def tavily_configured(self) -> bool:
        return self.tavily_enabled and bool(self.tavily_api_key.strip())

    @property
    def proxy_extra_list(self) -> list[str]:
        """Statements from PROXY_LIST_EXTRA, supporting newlines and commas."""

        return [
            item.strip()
            for line in self.proxy_list_extra.splitlines()
            for item in line.split(",")
            if item.strip()
        ]

    @property
    def fallback_provider_list(self) -> list[str]:
        """Configured fallback order, deduplicated and restricted to known names."""

        allowed = {"exa", "serper", "tavily"}
        ordered: list[str] = []
        for name in self.search_fallback_order.split(","):
            candidate = name.strip().lower()
            if candidate in allowed and candidate not in ordered:
                ordered.append(candidate)
        return ordered


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""

    return Settings()
