"""Health endpoint used by Docker and monitoring."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__
from app.models import CrawlStatus, HealthResponse, ProxyStatus
from app.services.crawler_service import CrawlerService
from app.services.proxy_manager import ProxyManager
from app.services.search import SearchService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service health")
async def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    service: SearchService = request.app.state.search_service
    local_up = await service.local_available()
    provider_status = service.provider_status()
    manager: ProxyManager | None = getattr(request.app.state, "proxy_manager", None)
    crawler: CrawlerService | None = getattr(request.app.state, "crawler", None)

    return HealthResponse(
        status="ok" if local_up else "degraded",
        meilisearch="up" if local_up else "down",
        brave="configured" if settings.brave_configured else "disabled",
        fallbacks={name: status for name, status in provider_status.items() if name != "brave"},
        index=settings.meili_index,
        proxy=ProxyStatus(**manager.status()) if manager is not None else None,
        crawl=CrawlStatus(**crawler.status()) if crawler is not None else None,
        version=__version__,
    )
