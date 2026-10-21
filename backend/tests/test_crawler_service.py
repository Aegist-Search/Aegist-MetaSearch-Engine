from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks

from app.api.routes.search import _crawl_seed, _schedule_crawl
from app.config import Settings
from app.ingestion.pipeline import IngestionReport
from app.models import SearchMeta, SearchPagination, SearchResponse, SearchResultItem
from app.services.crawler_service import CrawlerService, CrawlTarget


def make_report(documents: int = 2) -> IngestionReport:
    return IngestionReport(
        records=documents,
        documents=documents,
        indexed=documents,
        elapsed_s=0.01,
    )


class FakeRunner:
    """Stands in for ``katana``: records argv and writes a JSONL file."""

    def __init__(self, *, return_code: int = 0, write: bool = True, delay: float = 0.0) -> None:
        self.return_code = return_code
        self.write = write
        self.delay = delay
        self.calls: list[list[str]] = []

    async def __call__(self, argv: Sequence[str]) -> int:
        self.calls.append(list(argv))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.write:
            output = Path(argv[argv.index("-o") + 1])
            output.write_text('{"request": {"endpoint": "https://example.com/"}}\n', encoding="utf-8")
        return self.return_code


class FakeIngest:
    def __init__(self, documents: int = 2, *, error: Exception | None = None) -> None:
        self.documents = documents
        self.error = error
        self.paths: list[Path] = []
        self.existed: list[bool] = []

    def __call__(self, path: Path) -> IngestionReport:
        self.paths.append(path)
        self.existed.append(path.exists())
        if self.error is not None:
            raise self.error
        return make_report(self.documents)


class FakePublicCheck:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[str] = []

    async def __call__(self, url: str) -> bool:
        self.calls.append(url)
        return self.allowed


class SlowPublicCheck:
    """Simulates a hanging DNS resolver."""

    async def __call__(self, url: str) -> bool:
        await asyncio.sleep(5)
        return True


PublicCheckLike = Callable[[str], Awaitable[bool]]


def build_crawler(
    tmp_path: Path,
    *,
    runner: FakeRunner | None = None,
    ingest: FakeIngest | None = None,
    public: PublicCheckLike | None = None,
    binary: str | None = "/usr/local/bin/katana",
    **overrides: object,
) -> tuple[CrawlerService, FakeRunner, FakeIngest, PublicCheckLike]:
    options: dict[str, object] = {
        "crawl_enabled": True,
        "crawl_output_dir": str(tmp_path),
        "crawl_depth": 2,
        "crawl_domain_cooldown_s": 0,
        "crawl_failure_cooldown_s": 0,
        "crawl_max_concurrency": 2,
        "crawl_timeout_s": 5,
        "crawl_ingest_timeout_s": 5,
        "crawl_keep_outputs": True,
    }
    options.update(overrides)
    settings = Settings(_env_file=None, **options)  # type: ignore[arg-type]

    fake_runner = runner or FakeRunner()
    fake_ingest = ingest or FakeIngest()
    fake_public = public or FakePublicCheck()
    crawler = CrawlerService(
        settings,
        runner=fake_runner,
        ingest=fake_ingest,
        is_public=fake_public,
        binary_resolver=lambda _name: binary,
    )
    return crawler, fake_runner, fake_ingest, fake_public


class TestSchedule:
    async def test_reserves_a_normalized_public_url(self, tmp_path: Path) -> None:
        public = FakePublicCheck()
        crawler, _, _, _ = build_crawler(tmp_path, public=public)

        target = await crawler.schedule("HTTPS://WWW.Example.com/guida/?utm_source=news#top")

        assert target is not None
        assert target.url == "https://www.example.com/guida"
        assert target.domain == "example.com"
        # The request path does no DNS: the public check runs with the crawl.
        assert public.calls == []
        assert crawler.status()["running"] == 1

        outcome = await crawler.run(target)

        assert outcome.status == "completed"
        assert public.calls == ["https://www.example.com/guida"]
        assert crawler.status()["running"] == 0

    async def test_private_host_is_never_crawled(self, tmp_path: Path) -> None:
        crawler, runner, ingest, _ = build_crawler(tmp_path, public=FakePublicCheck(allowed=False))

        target = await crawler.schedule("http://127.0.0.1:7700/keys")
        assert target is not None

        outcome = await crawler.run(target)

        assert outcome.status == "skipped"
        assert outcome.reason == "not_public"
        assert runner.calls == []
        assert ingest.paths == []
        assert crawler.status()["last_reason"] == "not_public"

    async def test_free_text_is_not_a_crawl_target(self, tmp_path: Path) -> None:
        public = FakePublicCheck()
        crawler, _, _, _ = build_crawler(tmp_path, public=public)

        assert await crawler.schedule("privacy sul web") is None
        assert public.calls == []
        assert crawler.status()["last_reason"] == "invalid_url"

    async def test_disabled_crawler_rejects_everything(self, tmp_path: Path) -> None:
        public = FakePublicCheck()
        crawler, _, _, _ = build_crawler(tmp_path, public=public, crawl_enabled=False)

        assert await crawler.schedule("https://example.com/") is None
        assert public.calls == []
        assert crawler.status()["enabled"] is False
        assert crawler.status()["last_reason"] == "inactive"

    async def test_missing_binary_disables_the_crawler(self, tmp_path: Path) -> None:
        crawler, runner, _, _ = build_crawler(tmp_path, binary=None)

        assert await crawler.schedule("https://example.com/") is None
        assert runner.calls == []
        assert crawler.status()["binary"] is False
        assert crawler.status()["last_reason"] == "no_binary"

    async def test_slow_public_check_never_delays_schedule_and_is_bounded(self, tmp_path: Path) -> None:
        crawler, runner, _, _ = build_crawler(
            tmp_path,
            public=SlowPublicCheck(),
            crawl_dns_timeout_s=0.05,
        )

        started = time.monotonic()
        target = await crawler.schedule("https://example.com/")
        assert target is not None
        assert time.monotonic() - started < 1

        outcome = await asyncio.wait_for(crawler.run(target), timeout=2)

        assert outcome.status == "skipped"
        assert outcome.reason == "not_public"
        assert runner.calls == []
        assert crawler.status()["last_reason"] == "not_public"

    async def test_same_domain_is_not_crawled_twice_at_once(self, tmp_path: Path) -> None:
        crawler, _, _, _ = build_crawler(tmp_path)

        first = await crawler.schedule("https://example.com/a")
        second = await crawler.schedule("https://www.example.com/b")

        assert first is not None
        assert second is None
        assert crawler.status()["last_reason"] == "duplicate"

    async def test_global_concurrency_limit_skips_instead_of_queueing(self, tmp_path: Path) -> None:
        crawler, _, _, _ = build_crawler(tmp_path, crawl_max_concurrency=1)

        first = await crawler.schedule("https://a.example/")
        second = await crawler.schedule("https://b.example/")

        assert first is not None
        assert second is None
        assert crawler.status()["last_reason"] == "busy"

        await crawler.run(first)
        assert await crawler.schedule("https://b.example/") is not None

    async def test_start_rate_limit_skips_extra_crawls(self, tmp_path: Path) -> None:
        crawler, _, _, _ = build_crawler(
            tmp_path,
            crawl_max_starts_per_minute=1,
            crawl_start_burst=1,
            crawl_max_concurrency=4,
        )

        first = await crawler.schedule("https://a.example/")
        assert first is not None
        await crawler.run(first)

        second = await crawler.schedule("https://b.example/")

        assert second is None
        assert crawler.status()["last_reason"] == "rate_limited"
        assert crawler.status()["throttled"] == 1


class TestRun:
    async def test_runs_katana_and_indexes_its_output(self, tmp_path: Path) -> None:
        crawler, runner, ingest, _ = build_crawler(tmp_path, crawl_keep_outputs=False)
        target = await crawler.schedule("https://example.com/guida")
        assert target is not None

        outcome = await crawler.run(target)

        assert outcome.status == "completed"
        assert outcome.reason == "completed"
        assert outcome.documents == 2
        assert ingest.existed == [True]

        argv = runner.calls[0]
        assert argv[0] == "/usr/local/bin/katana"
        assert argv[argv.index("-u") + 1] == "https://example.com/guida"
        assert argv[argv.index("-d") + 1] == "2"
        assert "-jsonl" in argv
        assert argv[argv.index("-rl") + 1] == "20"
        assert argv[argv.index("-timeout") + 1] == "10"
        assert "User-Agent: AegisSearch/1.0" in argv

        output = Path(argv[argv.index("-o") + 1])
        assert output.parent == tmp_path
        assert output.name.startswith("dynamic_crawl-example.com-")
        # The JSONL is removed once Meilisearch owns the documents.
        assert not output.exists()
        assert ingest.paths == [output]
        assert crawler.status()["running"] == 0

    async def test_keeps_the_jsonl_when_requested(self, tmp_path: Path) -> None:
        crawler, runner, _, _ = build_crawler(tmp_path, crawl_keep_outputs=True)
        target = await crawler.schedule("https://example.com/")
        assert target is not None

        await crawler.run(target)

        output = Path(runner.calls[0][runner.calls[0].index("-o") + 1])
        assert output.exists()

    async def test_empty_crawl_is_skipped_without_ingestion(self, tmp_path: Path) -> None:
        crawler, _, ingest, _ = build_crawler(tmp_path, runner=FakeRunner(write=False))
        target = await crawler.schedule("https://example.com/")
        assert target is not None

        outcome = await crawler.run(target)

        assert outcome.status == "skipped"
        assert outcome.reason == "no_output"
        assert outcome.documents == 0
        assert ingest.paths == []

    async def test_katana_failure_is_reported(self, tmp_path: Path) -> None:
        crawler, _, ingest, _ = build_crawler(tmp_path, runner=FakeRunner(return_code=1))
        target = await crawler.schedule("https://example.com/")
        assert target is not None

        outcome = await crawler.run(target)

        assert outcome.status == "failed"
        assert outcome.reason == "katana_failed"
        assert ingest.paths == []

    async def test_timeout_cancels_katana_and_skips_ingestion(self, tmp_path: Path) -> None:
        crawler, _, ingest, _ = build_crawler(
            tmp_path,
            runner=FakeRunner(delay=5),
            crawl_timeout_s=0.05,
        )
        target = await crawler.schedule("https://example.com/")
        assert target is not None

        outcome = await asyncio.wait_for(crawler.run(target), timeout=2)

        assert outcome.status == "failed"
        assert outcome.reason == "timeout"
        assert ingest.paths == []
        assert crawler.status()["running"] == 0

    async def test_ingestion_failure_is_reported(self, tmp_path: Path) -> None:
        crawler, _, _, _ = build_crawler(
            tmp_path,
            ingest=FakeIngest(error=RuntimeError("meili down")),
        )
        target = await crawler.schedule("https://example.com/")
        assert target is not None

        outcome = await crawler.run(target)

        assert outcome.status == "failed"
        assert outcome.reason == "ingest_failed"

    async def test_zero_documents_still_completes(self, tmp_path: Path) -> None:
        crawler, _, _, _ = build_crawler(tmp_path, ingest=FakeIngest(documents=0))
        target = await crawler.schedule("https://example.com/")
        assert target is not None

        outcome = await crawler.run(target)

        assert outcome.status == "completed"
        assert outcome.documents == 0


class TestCooldowns:
    async def test_domain_cooldown_blocks_a_second_crawl(self, tmp_path: Path) -> None:
        crawler, _, _, _ = build_crawler(tmp_path, crawl_domain_cooldown_s=600)
        target = await crawler.schedule("https://example.com/a")
        assert target is not None
        await crawler.run(target)

        assert await crawler.schedule("https://example.com/b") is None
        assert crawler.status()["last_reason"] == "cooldown"
        # Another domain is still allowed.
        assert await crawler.schedule("https://other.example/") is not None

    async def test_failure_uses_the_longer_cooldown(self, tmp_path: Path) -> None:
        crawler, _, _, _ = build_crawler(
            tmp_path,
            runner=FakeRunner(return_code=2),
            crawl_domain_cooldown_s=0,
            crawl_failure_cooldown_s=600,
        )
        target = await crawler.schedule("https://example.com/")
        assert target is not None

        outcome = await crawler.run(target)

        assert outcome.reason == "katana_failed"
        assert await crawler.schedule("https://example.com/") is None
        assert crawler.status()["last_reason"] == "cooldown"


class TestCrawlSeed:
    @staticmethod
    def make_response(*, local_hits: int, urls: list[str]) -> SearchResponse:
        return SearchResponse(
            query="privacy",
            source="local" if local_hits else "brave",
            results=[
                SearchResultItem(
                    id=f"id-{index}",
                    title=f"Result {index}",
                    url=url,
                    source="local" if local_hits else "brave",
                    domain="example.com",
                )
                for index, url in enumerate(urls)
            ],
            pagination=SearchPagination(page=1, page_size=10, has_more=False),
            meta=SearchMeta(took_ms=1, local_hits=local_hits, brave_hits=len(urls)),
        )

    def test_explicit_url_query_is_the_seed(self) -> None:
        response = self.make_response(local_hits=3, urls=["https://example.com/local"])

        assert _crawl_seed("https://www.example.com/guida/?utm_source=x", response) == (
            "https://www.example.com/guida"
        )

    def test_empty_local_index_crawls_the_top_external_result(self) -> None:
        response = self.make_response(local_hits=0, urls=["https://exa.example/page"])

        assert _crawl_seed("privacy", response) == "https://exa.example/page"

    def test_free_text_with_local_results_is_not_crawled(self) -> None:
        response = self.make_response(local_hits=2, urls=["https://example.com/local"])

        assert _crawl_seed("privacy", response) is None

    def test_free_text_without_any_result_is_not_crawled(self) -> None:
        assert _crawl_seed("privacy", self.make_response(local_hits=0, urls=[])) is None


class StubCrawler:
    """Minimal stand-in used to test the route wiring, not the crawler itself."""

    def __init__(self) -> None:
        self.scheduled: list[str] = []

    async def schedule(self, url: str) -> CrawlTarget | None:
        self.scheduled.append(url)
        return CrawlTarget(url=url, domain="example.com")

    async def run(self, target: CrawlTarget) -> None:  # pragma: no cover - not awaited
        self.scheduled.append(target.url)


def make_request(crawler: StubCrawler) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(crawler=crawler)))


class TestRouteScheduling:
    async def test_queues_a_background_task_for_an_empty_local_index(self) -> None:
        crawler = StubCrawler()
        background = BackgroundTasks()
        response = TestCrawlSeed.make_response(local_hits=0, urls=["https://exa.example/page"])

        queued = await _schedule_crawl(
            make_request(crawler),
            background,
            "privacy",
            response,
            1,
            "auto",
        )

        assert queued is True
        assert crawler.scheduled == ["https://exa.example/page"]
        assert len(background.tasks) == 1

    async def test_local_engine_never_crawls(self) -> None:
        crawler = StubCrawler()
        background = BackgroundTasks()
        response = TestCrawlSeed.make_response(local_hits=0, urls=["https://exa.example/page"])

        queued = await _schedule_crawl(
            make_request(crawler),
            background,
            "privacy",
            response,
            1,
            "local",
        )

        assert queued is False
        assert crawler.scheduled == []
        assert background.tasks == []

    async def test_pages_after_the_first_are_not_seeds(self) -> None:
        crawler = StubCrawler()
        background = BackgroundTasks()
        response = TestCrawlSeed.make_response(local_hits=0, urls=["https://exa.example/page"])

        queued = await _schedule_crawl(
            make_request(crawler),
            background,
            "privacy",
            response,
            2,
            "auto",
        )

        assert queued is False
        assert crawler.scheduled == []

    async def test_explicit_url_is_queued_even_with_local_results(self) -> None:
        crawler = StubCrawler()
        background = BackgroundTasks()
        response = TestCrawlSeed.make_response(local_hits=3, urls=["https://example.com/local"])

        queued = await _schedule_crawl(
            make_request(crawler),
            background,
            "https://www.example.com/guida/?utm_source=x",
            response,
            1,
            "auto",
        )

        assert queued is True
        assert crawler.scheduled == ["https://www.example.com/guida"]


@pytest.mark.parametrize("cooldown", [0, 900])
async def test_release_always_frees_the_domain(tmp_path: Path, cooldown: float) -> None:
    crawler, _, _, _ = build_crawler(
        tmp_path,
        crawl_domain_cooldown_s=cooldown,
        crawl_keep_outputs=False,
    )
    target = await crawler.schedule("https://example.com/")
    assert target is not None

    await crawler.run(target)

    assert crawler.status()["running"] == 0
    assert crawler.status()["reserved"] == 0
