"""On-demand Katana crawl, scheduled in background from the search API.

The flow is deliberately split in two phases:

1. ``schedule()`` decides whether the request may crawl at all and *reserves*
   the domain. It is cheap, never starts a process, and is awaited by the
   request handler so the response can report an accurate
   ``meta.crawl_scheduled`` flag.
2. ``run()`` is handed to FastAPI's ``BackgroundTasks``: it spawns Katana with
   ``asyncio.create_subprocess_exec``, waits with a hard timeout, then feeds the
   JSONL file to the same ingestion pipeline as the CLI.

Resource guards (a public search box must not turn the server into a crawler
farm):

* a global token bucket limits crawl *starts* (``CRAWL_MAX_STARTS_PER_MINUTE``
  with a ``CRAWL_START_BURST`` burst); excess starts are skipped, not queued;
* ``CRAWL_MAX_CONCURRENCY`` crawls reserved/running at once, process-wide;
* one crawl per domain at a time;
* a cooldown per domain after each run, longer after a failure;
* only http(s) URLs whose DNS resolves to public addresses, so the crawler
  cannot read internal services;
* a wall-clock timeout on Katana and a separate one on ingestion.

Privacy: this module never logs a query, a URL, a hostname or a proxy address.
Only reason codes and counters are logged, so an operator cannot reconstruct
what the user searched for from the backend logs.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import shutil
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.ingestion.pipeline import IngestionReport, ingest_katana_file
from app.services.rate_limit import TokenBucket
from app.utils.net import is_public_url
from app.utils.urls import domain_of, normalize_url

logger = logging.getLogger(__name__)

CrawlStatus = Literal["completed", "skipped", "failed"]
CrawlReason = Literal[
    "completed",
    "no_output",
    "no_binary",
    "katana_failed",
    "timeout",
    "ingest_failed",
    "rate_limited",
    "output_unavailable",
    "inactive",
    "busy",
    "cooldown",
    "duplicate",
    "not_public",
    "invalid_url",
    "cancelled",
]

# "The site answered but there was nothing to index": normal cooldown.
_SLOW_REASONS = frozenset({"completed", "no_output"})

CrawlRunner = Callable[[Sequence[str]], Awaitable[int]]
CrawlIngest = Callable[[Path], IngestionReport]
PublicCheck = Callable[[str], Awaitable[bool]]
BinaryResolver = Callable[[str], "str | None"]


@dataclass(frozen=True, slots=True)
class CrawlTarget:
    """A reserved crawl: the seed URL and the domain holding the reservation."""

    url: str
    domain: str


@dataclass(frozen=True, slots=True)
class CrawlOutcome:
    status: CrawlStatus
    reason: CrawlReason
    documents: int = 0


class CrawlerService:
    def __init__(
        self,
        settings: Settings,
        *,
        runner: CrawlRunner | None = None,
        ingest: CrawlIngest | None = None,
        is_public: PublicCheck | None = None,
        binary_resolver: BinaryResolver = shutil.which,
    ) -> None:
        self._settings = settings
        # The hooks exist for tests: production uses the real subprocess,
        # the real pipeline and the real DNS guard.
        self._runner = runner or self._run_katana
        self._ingest = ingest or self._ingest_file
        self._is_public = is_public or is_public_url
        self._binary_resolver = binary_resolver

        self._binary: str | None = None
        self._binary_checked = False

        # domain -> monotonic reservation time. A reservation leaked by a
        # request that never ran its background task is expired automatically.
        self._reserved: dict[str, float] = {}
        self._next_allowed: dict[str, float] = {}
        self._tasks: set[asyncio.Task[object]] = set()
        self._closed = False
        self._last_reason: CrawlReason | None = None

        # Token bucket on crawl starts. It is a single counter: no IP, no
        # domain, so it cannot build a profile of the caller.
        self._limiter = TokenBucket(
            settings.crawl_start_burst,
            settings.crawl_max_starts_per_minute,
        )
        self._throttled = 0
        self._rate_limit_logged_at: float | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Log whether the crawler is usable; never blocks and never raises."""

        if not self._settings.crawl_enabled:
            logger.info("On-demand crawl disabled (CRAWL_ENABLED=false)")
            return
        if self._binary_path() is None:
            return  # _binary_path already logged the missing executable
        logger.info(
            "On-demand crawl ready (max %d concurrent, depth %d)",
            self._settings.crawl_max_concurrency,
            self._settings.crawl_depth,
        )

    async def stop(self) -> None:
        """Cancel in-flight crawls so shutdown does not leave child processes."""

        self._closed = True
        tasks = [task for task in self._tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def status(self) -> dict[str, object]:
        """Counters for the health endpoint (never contains addresses)."""

        self._expire_reservations()
        return {
            "enabled": self._settings.crawl_enabled,
            "binary": self._binary_path(log_missing=False) is not None,
            "running": len(self._reserved),
            "reserved": len(self._reserved),
            "throttled": self._throttled,
            "last_reason": self._last_reason,
        }

    # -- phase 1: decide and reserve ---------------------------------------
    async def schedule(self, url: str) -> CrawlTarget | None:
        """Validate ``url`` and reserve its domain, or return ``None``.

        Deliberately does **no network I/O**: the public-address check runs in
        ``run()``, in the background, so the search response never waits for a
        DNS resolver. The caller must pass the returned target to ``run()``; a
        reservation whose run never starts is released by
        ``_expire_reservations``.
        """

        settings = self._settings
        if self._closed or not settings.crawl_enabled:
            return self._skip("inactive")

        normalized = normalize_url(url)
        if not normalized:
            return self._skip("invalid_url")
        domain = domain_of(normalized)
        if not domain:
            return self._skip("invalid_url")

        blocked = self._blocked_reason(domain)
        if blocked is not None:
            return self._skip(blocked)

        # The bucket is global: it protects the server without storing any
        # client identifier. A rejected start is skipped, never queued.
        if not self._limiter.take():
            return self._skip(self._throttle())

        # Reservation happens after a single synchronous check, so two
        # concurrent requests cannot reserve the same domain or exceed the
        # concurrency limit.
        self._reserved[domain] = time.monotonic()
        return CrawlTarget(url=normalized, domain=domain)

    def _throttle(self) -> CrawlReason:
        """Count a start rejected by the token bucket.

        The warning is throttled to once a minute so a flood of searches does
        not flood the logs, and it contains no address or query.
        """

        self._throttled += 1
        now = time.monotonic()
        if self._rate_limit_logged_at is None or now - self._rate_limit_logged_at >= 60.0:
            self._rate_limit_logged_at = now
            logger.warning("Crawl start rate limit reached: new crawls are being skipped")
        return "rate_limited"

    def _blocked_reason(self, domain: str) -> CrawlReason | None:
        # One crawl per domain, and never more than the global budget: a busy
        # server skips new crawls instead of queueing an unbounded backlog.
        self._expire_reservations()
        if domain in self._reserved:
            return "duplicate"
        if len(self._reserved) >= self._settings.crawl_max_concurrency:
            return "busy"
        if self._next_allowed.get(domain, 0.0) > time.monotonic():
            return "cooldown"
        if self._binary_path() is None:
            return "no_binary"
        return None

    def _skip(self, reason: CrawlReason) -> None:
        self._last_reason = reason
        return None

    # -- phase 2: crawl and ingest -----------------------------------------
    async def run(self, target: CrawlTarget) -> CrawlOutcome:
        """Execute a reserved crawl. Never raises, except on cancellation."""

        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)

        output = self._output_path(target)
        try:
            reason, documents = await self._execute(target, output)
        except asyncio.CancelledError:
            reason, documents = "cancelled", 0
            self._release(target, reason="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - background must never bubble up
            logger.warning("Dynamic crawl crashed (%s)", type(exc).__name__)
            reason, documents = "katana_failed", 0
        finally:
            if not self._settings.crawl_keep_outputs:
                with contextlib.suppress(OSError):
                    output.unlink(missing_ok=True)
            if task is not None:
                self._tasks.discard(task)

        self._release(target, reason=reason)
        self._last_reason = reason
        if reason == "completed":
            status: CrawlStatus = "completed"
        elif reason in {"no_output", "not_public"}:
            status = "skipped"
        else:
            status = "failed"
        return CrawlOutcome(status=status, reason=reason, documents=documents)

    async def _execute(self, target: CrawlTarget, output: Path) -> tuple[CrawlReason, int]:
        """Run Katana and ingest its output, mapping every failure to a reason."""

        try:
            public = await asyncio.wait_for(
                self._is_public(target.url),
                timeout=self._settings.crawl_dns_timeout_s,
            )
        except Exception:  # noqa: BLE001 - includes timeouts and resolver errors
            public = False
        if not public:
            logger.warning("Dynamic crawl skipped: target is not a public host")
            return "not_public", 0

        try:
            output.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Dynamic crawl cannot write to its output directory")
            return "output_unavailable", 0

        try:
            return_code = await asyncio.wait_for(
                self._runner(self._build_command(target, output)),
                timeout=self._settings.crawl_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Dynamic crawl timed out after %.0fs",
                self._settings.crawl_timeout_s,
            )
            return "timeout", 0
        except asyncio.CancelledError:
            raise
        except FileNotFoundError:
            logger.warning("Dynamic crawl skipped: Katana executable not found")
            return "no_binary", 0
        except OSError as exc:
            logger.warning("Dynamic crawl failed to start (%s)", type(exc).__name__)
            return "katana_failed", 0

        if return_code != 0:
            logger.warning("Dynamic crawl failed: Katana exited with code %d", return_code)
            return "katana_failed", 0
        if not output.is_file() or output.stat().st_size == 0:
            logger.info("Dynamic crawl produced no page")
            return "no_output", 0

        try:
            report = await asyncio.wait_for(
                asyncio.to_thread(self._ingest, output),
                timeout=self._settings.crawl_ingest_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning("Dynamic crawl ingestion timed out")
            return "ingest_failed", 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - background must not raise
            logger.warning("Dynamic crawl ingestion failed (%s)", type(exc).__name__)
            return "ingest_failed", 0

        logger.info(
            "Dynamic crawl indexed %d documents in %.1fs",
            report.indexed,
            report.elapsed_s,
        )
        return "completed", report.indexed

    def _release(self, target: CrawlTarget, *, reason: CrawlReason) -> None:
        """Free the reservation and record the next allowed run for the domain."""

        self._reserved.pop(target.domain, None)

        if reason != "cancelled":
            cooldown = (
                self._settings.crawl_domain_cooldown_s
                if reason in _SLOW_REASONS
                else self._settings.crawl_failure_cooldown_s
            )
            self._next_allowed[target.domain] = time.monotonic() + cooldown
            self._prune()

    def _expire_reservations(self) -> None:
        """Drop reservations whose background task never ran.

        Starlette runs background tasks after sending the response; a client
        disconnect or a server error in that window can skip them. Without an
        expiry the domain (and a slot of ``CRAWL_MAX_CONCURRENCY``) would stay
        blocked forever.
        """

        if not self._reserved:
            return
        limit = (
            self._settings.crawl_timeout_s
            + self._settings.crawl_ingest_timeout_s
            + 120.0
        )
        now = time.monotonic()
        for domain, reserved_at in list(self._reserved.items()):
            if now - reserved_at > limit:
                logger.warning("Released a stale crawl reservation")
                del self._reserved[domain]

    def _prune(self) -> None:
        """Keep the cooldown map bounded on a long-running process."""

        if len(self._next_allowed) <= 512:
            return
        now = time.monotonic()
        for domain, deadline in list(self._next_allowed.items()):
            if deadline <= now:
                del self._next_allowed[domain]

    # -- Katana ------------------------------------------------------------
    def _binary_path(self, *, log_missing: bool = True) -> str | None:
        if not self._binary_checked:
            self._binary = self._binary_resolver(self._settings.katana_binary)
            self._binary_checked = True
            if self._binary is None and log_missing:
                logger.warning(
                    "Katana executable '%s' not found: dynamic crawl disabled",
                    self._settings.katana_binary,
                )
        return self._binary

    def _build_command(self, target: CrawlTarget, output: Path) -> list[str]:
        settings = self._settings
        return [
            self._binary_path() or settings.katana_binary,
            "-u",
            target.url,
            "-d",
            str(settings.crawl_depth),
            "-jsonl",
            "-o",
            str(output),
            # No colours and no per-URL output on stdout: the file is the only
            # output we read, and stdout stays at /dev/null.
            "-nc",
            "-silent",
            "-rl",
            str(settings.crawl_rate_limit),
            "-timeout",
            str(settings.crawl_request_timeout_s),
            "-retry",
            "1",
            "-H",
            f"User-Agent: {settings.user_agent}",
        ]

    async def _run_katana(self, argv: Sequence[str]) -> int:
        """Spawn Katana without blocking the loop and kill it on cancellation."""

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            # stderr may contain crawled URLs: never persist or log it.
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return await process.wait()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.wait()
            raise

    def _output_path(self, target: CrawlTarget) -> Path:
        """Per-run file: concurrent crawls can never interleave records.

        The requested layout is ``/data/dynamic_crawl.jsonl``; the domain and a
        random token are appended so ``CRAWL_MAX_CONCURRENCY`` can stay above 1.
        """

        slug = re.sub(r"[^a-z0-9.-]+", "-", target.domain.lower()).strip("-.")[:60] or "site"
        token = hashlib.sha1(f"{target.url}{time.time_ns()}".encode("utf-8")).hexdigest()[:10]
        directory = Path(self._settings.crawl_output_dir).expanduser()
        return directory / f"{self._settings.crawl_output_prefix}-{slug}-{token}.jsonl"

    def _ingest_file(self, path: Path) -> IngestionReport:
        settings = self._settings
        return ingest_katana_file(
            path,
            index=settings.meili_index,
            meili_url=settings.meili_url,
            master_key=settings.meili_master_key,
            batch_size=settings.crawl_ingest_batch_size,
            # Settings are applied when the index is created; rewriting them on
            # every crawl would force Meilisearch to rebuild its internal data.
            apply_settings=False,
            # Repeated crawls of the same page must not re-upload it: the hash
            # lookup is one request per batch (usually a single page here).
            skip_unchanged=True,
            dedupe=settings.dedupe_enabled,
            dedupe_threshold=settings.dedupe_threshold,
            dedupe_prefix_chars=settings.dedupe_prefix_chars,
        )
