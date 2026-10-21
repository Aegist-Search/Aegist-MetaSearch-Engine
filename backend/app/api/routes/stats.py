"""Aggregate counters for monitoring: totals only, no queries and no addresses."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.models import MetricsResponse
from app.services.metrics import SearchMetrics

router = APIRouter(tags=["stats"])


@router.get(
    "/stats",
    response_model=MetricsResponse,
    summary="Aggregate search counters",
    description=(
        "In-memory counters since process start: total searches, local and "
        "external hits, empty and failed searches, and how many times each "
        "provider failed. The payload never contains a query, a URL, a result "
        "or a client address, so it can be scraped by monitoring without "
        "weakening the privacy guarantees of the engine."
    ),
)
async def stats(request: Request) -> MetricsResponse:
    metrics: SearchMetrics | None = getattr(request.app.state, "metrics", None)
    if metrics is None:
        # No lifespan (tests, a custom app): report zeroes instead of failing.
        metrics = SearchMetrics()
    return MetricsResponse(**metrics.snapshot())  # type: ignore[arg-type]
