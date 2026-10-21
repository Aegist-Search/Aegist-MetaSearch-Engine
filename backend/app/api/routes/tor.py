"""Tor connectivity endpoint used by the frontend badge."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.models import TorStatus
from app.services.tor import TorStatusService

router = APIRouter(tags=["tor"])


@router.get(
    "/tor-status",
    response_model=TorStatus,
    summary="Tor exit-node status",
    description=(
        "Asks check.torproject.org through the configured SOCKS5 proxy whether "
        "the exit node belongs to the Tor network and which address it uses. "
        "The answer is cached for TOR_CACHE_TTL_S so polling does not open a "
        "new circuit on every request. A stopped Tor proxy returns "
        "tor_active=false with a reason code; it never blocks the application."
    ),
)
async def tor_status(request: Request) -> TorStatus:
    service: TorStatusService | None = getattr(request.app.state, "tor_status", None)
    if service is None:
        return TorStatus(tor_active=False, error="tor_disabled")

    result = await service.check()
    return TorStatus(
        tor_active=result.active,
        exit_ip=result.exit_ip,
        latency_ms=result.latency_ms,
        error=result.error,
    )
