from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes.stats import stats
from app.config import Settings
from app.models import MetricsResponse
from app.services.metrics import SearchMetrics
from app.services.search import SearchService, SearchUnavailable
from tests.test_search_service import FakeBrave, FakeFallback, FakeLocal, hit

METRIC_FIELDS = {
    "uptime_s",
    "searches_total",
    "searches_failed_total",
    "searches_degraded_total",
    "searches_empty_total",
    "local_hits_total",
    "external_hits_total",
    "provider_errors_total",
}


def build_metrics_service(
    local: FakeLocal,
    brave: FakeBrave,
    *,
    metrics: SearchMetrics | None = None,
    fallbacks: tuple[FakeFallback, ...] = (),
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
        metrics=metrics,
    )


def test_snapshot_starts_at_zero() -> None:
    snapshot = SearchMetrics().snapshot()

    assert snapshot["searches_total"] == 0
    assert snapshot["searches_failed_total"] == 0
    assert snapshot["provider_errors_total"] == {}
    assert set(snapshot) == METRIC_FIELDS


def test_record_search_aggregates_counts_and_names_only() -> None:
    metrics = SearchMetrics()

    metrics.record_search(
        local_hits=2,
        external_hits=3,
        provider_errors=["brave", "brave"],
        degraded=True,
        empty=False,
    )
    metrics.record_search(local_hits=0, external_hits=0, empty=True)

    snapshot = metrics.snapshot()
    assert snapshot["searches_total"] == 2
    assert snapshot["searches_empty_total"] == 1
    assert snapshot["searches_degraded_total"] == 1
    assert snapshot["local_hits_total"] == 2
    assert snapshot["external_hits_total"] == 3
    # A provider failing twice in the same search is one failing search, not two.
    assert snapshot["provider_errors_total"] == {"brave": 1}


def test_record_failure_counts_the_attempt() -> None:
    metrics = SearchMetrics()

    metrics.record_failure()

    snapshot = metrics.snapshot()
    assert snapshot["searches_total"] == 1
    assert snapshot["searches_failed_total"] == 1


def test_snapshot_carries_no_user_data() -> None:
    metrics = SearchMetrics()
    metrics.record_search(local_hits=1, external_hits=0, provider_errors=["exa"])

    payload = metrics.snapshot()

    assert set(payload) == METRIC_FIELDS
    assert all(not isinstance(value, (list, tuple)) for value in payload.values())


@pytest.mark.asyncio
async def test_search_service_records_local_and_external_hits() -> None:
    metrics = SearchMetrics()
    local = FakeLocal([hit("https://example.com/a"), hit("https://example.com/b")], total=2)
    brave = FakeBrave([hit("https://brave.example/1")])

    await build_metrics_service(local, brave, metrics=metrics).search("privacy")

    snapshot = metrics.snapshot()
    assert snapshot["searches_total"] == 1
    assert snapshot["local_hits_total"] == 2
    assert snapshot["external_hits_total"] == 1
    assert snapshot["provider_errors_total"] == {}
    assert snapshot["searches_degraded_total"] == 0


@pytest.mark.asyncio
async def test_search_service_counts_provider_errors_once_per_search() -> None:
    metrics = SearchMetrics()
    local = FakeLocal()
    brave = FakeBrave(error="brave_rate_limited")
    exa = FakeFallback("exa", [hit("https://exa.example/1")])

    service = build_metrics_service(local, brave, metrics=metrics, fallbacks=(exa,))
    await service.search("privacy")
    await service.search("privacy")

    snapshot = metrics.snapshot()
    assert snapshot["provider_errors_total"] == {"brave": 2}
    assert snapshot["searches_total"] == 2


@pytest.mark.asyncio
async def test_unavailable_search_is_counted_as_a_failure() -> None:
    metrics = SearchMetrics()
    local = FakeLocal(error="meilisearch_connecterror")
    brave = FakeBrave(error="brave_rate_limited")

    with pytest.raises(SearchUnavailable):
        await build_metrics_service(local, brave, metrics=metrics).search("privacy")

    snapshot = metrics.snapshot()
    assert snapshot["searches_total"] == 1
    assert snapshot["searches_failed_total"] == 1


def test_metrics_are_optional_on_the_search_service() -> None:
    service = build_metrics_service(FakeLocal(), FakeBrave())

    assert service._metrics is None  # noqa: SLF001 - the wiring itself is the assertion


@pytest.mark.asyncio
async def test_stats_endpoint_returns_zeroes_without_a_lifespan() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    response = await stats(request)  # type: ignore[arg-type]

    assert isinstance(response, MetricsResponse)
    assert response.searches_total == 0
    assert response.provider_errors_total == {}


@pytest.mark.asyncio
async def test_stats_endpoint_reads_the_process_counters() -> None:
    metrics = SearchMetrics()
    metrics.record_search(local_hits=4, external_hits=0, empty=False)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(metrics=metrics)))

    response = await stats(request)  # type: ignore[arg-type]

    assert response.searches_total == 1
    assert response.local_hits_total == 4
