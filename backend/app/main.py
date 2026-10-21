"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import health, preview, search, stats, suggest, tor
from app.config import get_settings
from app.middleware import rate_limit_middleware
from app.services.brave import BraveClient
from app.services.crawler_service import CrawlerService
from app.services.exa import ExaClient
from app.services.meili import MeilisearchClient
from app.services.metrics import SearchMetrics
from app.services.preview import PreviewService
from app.services.provider import SearchProvider
from app.services.proxy_manager import ProxiedHttpClient, ProxyManager
from app.services.rate_limit import ClientRateLimiter
from app.services.search import SearchService
from app.services.serper import SerperClient
from app.services.tavily import TavilyClient
from app.services.tor import TorStatusService


def _configure_logging() -> None:
    """Privacy-first logging.

    * uvicorn access logs (method, path, client IP) are disabled in code, not
      only through the CLI flag, so an accidental plain ``uvicorn`` launch
      cannot start collecting IPs.
    * httpx logs the full request URL at INFO level, which would include the
      user query sent to external providers: keep it at WARNING.
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


_configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(settings.request_timeout_s),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        follow_redirects=False,
    ) as http:
        # Only the external provider calls are proxied. Meilisearch stays
        # local and the preview downloader keeps its own strict SSRF checks.
        proxy_manager = ProxyManager(settings, http=http)
        await proxy_manager.start()
        proxied = ProxiedHttpClient(
            proxy_manager,
            direct=http,
            settings=settings,
        )

        local = MeilisearchClient(http, settings)
        brave = BraveClient(proxied, settings)

        fallbacks: list[SearchProvider] = []
        for name in settings.fallback_provider_list:
            if name == "exa":
                # exa-py has no proxy option: ExaClient clones the SDK's own
                # httpx client onto the selected SOCKS5 proxy per attempt.
                fallbacks.append(ExaClient(settings, proxies=proxy_manager))
            elif name == "serper":
                fallbacks.append(SerperClient(proxied, settings))
            elif name == "tavily":
                fallbacks.append(TavilyClient(proxied, settings))

        # On-demand crawls are pure background work: the service holds only
        # counters and cooldowns, and stops in-flight Katana processes on
        # shutdown. It never delays a search response.
        crawler = CrawlerService(settings)
        crawler.start()

        # Diagnostics only: no network call at startup, so a stopped Tor proxy
        # can never delay or break the application.
        tor_status = TorStatusService(settings)

        # Aggregate counters stay in process memory and never hold a query.
        metrics = SearchMetrics()

        app.state.settings = settings
        app.state.metrics = metrics
        app.state.proxy_manager = proxy_manager
        app.state.crawler = crawler
        app.state.tor_status = tor_status
        app.state.preview = PreviewService(
            http,
            timeout_s=min(settings.request_timeout_s, 8.0),
        )
        app.state.search_service = SearchService(
            settings=settings,
            local=local,
            brave=brave,
            fallbacks=fallbacks,
            metrics=metrics,
        )
        try:
            yield
        finally:
            await crawler.stop()
            await proxy_manager.stop()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Privacy-first hybrid search API (Meilisearch + multi-provider external search shield).",
        lifespan=lifespan,
        redoc_url=None,
    )

    if settings.rate_limit_enabled:
        # Only the endpoints that can spend provider quota are limited. Added
        # before the privacy middleware, because the last-added middleware is
        # the outermost one: a 429 must still carry the privacy headers.
        limiter = ClientRateLimiter(
            burst=settings.rate_limit_burst,
            refill_per_minute=settings.rate_limit_requests_per_minute,
            max_clients=settings.rate_limit_max_clients,
        )
        app.middleware("http")(
            rate_limit_middleware(
                limiter,
                paths={"/api/v1/search", "/api/v1/suggest"},
            )
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
        max_age=600,
    )

    @app.middleware("http")
    async def privacy_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path == "/api/v1/preview":
            # Preview images are public assets fetched and cached by the backend:
            # letting the browser reuse them avoids re-downloading on every render.
            response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        else:
            response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Permissions-Policy"] = "interest-cohort=(), browsing-topics=()"
        if "set-cookie" in response.headers:
            del response.headers["set-cookie"]
        return response

    app.include_router(search.router, prefix="/api/v1")
    app.include_router(suggest.router, prefix="/api/v1")
    app.include_router(preview.router, prefix="/api/v1")
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(stats.router, prefix="/api/v1")
    app.include_router(tor.router, prefix="/api/v1")

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": __version__,
            "docs": "/docs",
            "search": "/api/v1/search?q=privacy",
        }

    return app


app = create_app()
