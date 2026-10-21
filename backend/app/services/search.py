"""Hybrid search orchestration: local index first, external providers as a shield."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections.abc import Sequence
from dataclasses import replace

from app.config import Settings
from app.models import (
    HighlightSegment,
    ResultSource,
    SearchEngine,
    SearchInsight,
    SearchMeta,
    SearchPagination,
    SearchResponse,
    SearchResultItem,
    SearchSort,
)
from app.services.brave import BraveClient
from app.services.circuit import CircuitBreaker
from app.services.errors import BackendError
from app.services.meili import MeilisearchClient
from app.services.metrics import SearchMetrics
from app.services.provider import SearchProvider
from app.services.provider_cache import ProviderCache, cache_key
from app.services.results import BackendResult, RawHit
from app.services.spelling import closest_word, tolerance_for
from app.utils.query import is_excluded
from app.utils.urls import domain_of, stable_id, url_key

logger = logging.getLogger(__name__)

_MAX_INSIGHTS = 6
_MAX_EXTRA_SNIPPETS = 8
# The spelling lookup runs only on an empty page: keep it bounded to a few
# short parallel searches, and ignore words too short to have a safe typo.
_MAX_CORRECTION_TERMS = 4
_MIN_CORRECTION_TERM_LENGTH = 5

# --- Local rerank ---------------------------------------------------------
# The local block is ordered by a combined score instead of the raw
# Meilisearch order: relevance stays the main signal, but a query match in the
# title, a recently crawled page and the diversity of the domain list can move
# a result up or down. Weights are kept as constants so the ranking is easy to
# reason about and test; none of them is user-configurable.
_RERANK_TITLE_WEIGHT = 0.25
_RERANK_FRESHNESS_WEIGHT = 0.20
_RERANK_DOMAIN_STEP = 0.06
_RERANK_DOMAIN_CAP = 0.30
_RERANK_FRESHNESS_HALF_LIFE_DAYS = 30.0
_SECONDS_PER_DAY = 86_400.0

# --- External merge -------------------------------------------------------
# External results are ordered by a provider weight minus a positional decay
# instead of plain round-robin, so a fast provider with weaker results cannot
# take the top slots only because it answered first. Weights stay close to 1
# and the step is small: the provider order is still respected among results
# at the same position, while a deep hit from a provider can fall below the
# first hit of the next one. Local results always keep the leading slots.
_PROVIDER_WEIGHTS = {"brave": 1.0, "exa": 0.97, "serper": 0.94, "tavily": 0.91}
_PROVIDER_DEFAULT_WEIGHT = 0.90
_PROVIDER_POSITION_STEP = 0.03


def query_terms(query: str) -> tuple[str, ...]:
    """Lowercase, de-duplicated query words used by the local rerank."""

    return tuple(dict.fromkeys(term for term in query.casefold().split() if term))


def title_match(title: str, terms: Sequence[str]) -> float:
    """Share of the query terms appearing in the title, from 0 to 1."""

    if not terms:
        return 0.0
    haystack = title.casefold()
    return sum(1 for term in terms if term in haystack) / len(terms)


def freshness_score(crawled_at: int, *, now: float) -> float:
    """Exponential freshness: 1.0 for a page crawled now, 0.5 after 30 days.

    Pages without a timestamp (``crawled_at <= 0``) score 0 instead of being
    treated as very old, so they are not punished twice by the domain penalty.
    """

    if crawled_at <= 0:
        return 0.0
    age_days = max(0.0, (now - crawled_at) / _SECONDS_PER_DAY)
    return 0.5 ** (age_days / _RERANK_FRESHNESS_HALF_LIFE_DAYS)


def rerank_local(
    hits: Sequence[RawHit],
    query: str,
    *,
    now: float | None = None,
) -> list[RawHit]:
    """Order local hits by relevance, title match, freshness and domain variety.

    Pure function: no I/O and no mutation of the input, deterministic for a
    given ``now`` and input order. The score is

        rank + 0.25 * title_match + 0.20 * freshness - domain_penalty

    where ``rank`` is Meilisearch's ``_rankingScore`` clamped to 0..1 (a
    missing score counts as 0) and the penalty grows by 0.06 for every earlier
    hit from the same domain, capped at 0.30: a site that already fills the
    page is pushed down without being excluded. Ties keep the incoming order.
    """

    if not hits:
        return []

    current = time.time() if now is None else now
    terms = query_terms(query)
    repeated: dict[str, int] = {}

    scored: list[tuple[float, int, RawHit]] = []
    for index, hit in enumerate(hits):
        rank = hit.rank if hit.rank is not None else 0.0
        rank = min(1.0, max(0.0, rank))

        domain = hit.domain.casefold()
        seen = repeated.get(domain, 0)
        repeated[domain] = seen + 1
        penalty = min(_RERANK_DOMAIN_CAP, _RERANK_DOMAIN_STEP * seen)

        score = (
            rank
            + _RERANK_TITLE_WEIGHT * title_match(hit.title, terms)
            + _RERANK_FRESHNESS_WEIGHT * freshness_score(hit.crawled_at, now=current)
            - penalty
        )
        scored.append((score, index, hit))

    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [hit for _, _, hit in scored]


class SearchUnavailable(RuntimeError):
    """Raised when no backend could produce an answer."""


class SearchService:
    def __init__(
        self,
        *,
        settings: Settings,
        local: MeilisearchClient,
        brave: BraveClient,
        fallbacks: Sequence[SearchProvider] = (),
        cache: ProviderCache | None = None,
        breaker: CircuitBreaker | None = None,
        metrics: SearchMetrics | None = None,
    ) -> None:
        self._settings = settings
        self._local = local
        self._brave = brave
        self._fallbacks = tuple(fallbacks)
        # Counters only: the recorder has no way to store a query or an address.
        self._metrics = metrics
        self._cache = (
            cache
            if cache is not None
            else (
                ProviderCache(
                    ttl_s=settings.provider_cache_ttl_s,
                    max_entries=settings.provider_cache_max_entries,
                )
                if settings.provider_cache_ttl_s > 0
                else None
            )
        )
        self._breaker = breaker or CircuitBreaker(
            failure_threshold=settings.provider_failure_threshold,
            cooldown_s=settings.provider_cooldown_s,
        )

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int | None = None,
        engine: SearchEngine = "auto",
        sort: SearchSort = "relevance",
        filter: str | None = None,
        excluded: Sequence[str] = (),
    ) -> SearchResponse:
        started = time.perf_counter()

        size = max(1, min(page_size or self._settings.page_size, 20))
        page = max(1, min(page, self._settings.max_page))
        offset = (page - 1) * size

        mode = self._settings.search_mode
        candidates = self._candidates()
        explicit_external = False
        if engine == "local":
            candidates = []
        elif engine == "multi":
            mode = "parallel"
            explicit_external = True
        elif engine in {"brave", "exa", "serper", "tavily"}:
            candidates = [candidate for candidate in candidates if candidate[0] == engine]
            mode = "fallback"
            explicit_external = True

        # A full local page would otherwise crowd out every external result.
        # For an explicit multi/provider engine, keep half the page for local
        # results and leave the other half to the external providers.
        local_limit = size
        local_offset = offset
        external_size = size
        external_offset = offset
        if explicit_external and candidates:
            local_limit = max(1, size // 2)
            local_offset = (page - 1) * local_limit
            external_size = max(1, size - local_limit)
            external_offset = (page - 1) * external_size

        # ``crawled_at:desc`` is an explicit chronological request: Meilisearch
        # applies it and the merge keeps that order (no rerank on top).
        local_sort = ["crawled_at:desc"] if sort == "crawled_at:desc" else None

        degraded = False
        local_result = BackendResult()
        try:
            local_result = await self._local.search(
                query,
                limit=local_limit,
                offset=local_offset,
                sort=local_sort,
                filter=filter,
            )
        except BackendError as exc:
            degraded = True
            logger.warning("Local index unavailable: %s", exc.reason)

        # ``-term`` cannot be expressed as a Meilisearch filter (there is no
        # negative full-text operator), so the excluded terms are applied to
        # the local page after the search. External results are unaffected:
        # the operators describe the local index only.
        if excluded and local_result.hits:
            local_result = replace(
                local_result,
                hits=[
                    hit
                    for hit in local_result.hits
                    if not is_excluded(
                        (hit.title, hit.h1, hit.snippet, hit.domain), excluded
                    )
                ],
            )

        provider_results: list[tuple[str, BackendResult]] = []
        provider_hits: dict[str, int] = {}
        provider_errors: list[str] = []
        external_attempted = False

        # `auto` keeps the local-first shield. An explicit engine choice is a
        # user request: multi/provider selections run even when the local index
        # already has enough results.
        should_search_external = bool(candidates) and (
            engine != "auto" or len(local_result.hits) < self._settings.min_local_results
        )

        if should_search_external:
            external_attempted = True
            local_keys = {url_key(hit.url) for hit in local_result.hits}

            if mode == "parallel":
                (
                    provider_results,
                    parallel_degraded,
                    provider_errors,
                ) = await self._search_parallel(
                    candidates,
                    query,
                    size=external_size,
                    offset=external_offset,
                    page=page,
                )
                degraded = degraded or parallel_degraded
                provider_hits = {
                    name: len(result.hits) for name, result in provider_results
                }
            else:
                (
                    provider_results,
                    provider_hits,
                    fallback_degraded,
                    provider_errors,
                ) = await self._search_fallback(
                    candidates,
                    query,
                    size=external_size,
                    offset=external_offset,
                    page=page,
                    local_keys=local_keys,
                )
                degraded = degraded or fallback_degraded

        merged = self._rerank_merged(
            self._merge(local_result.hits, provider_results),
            query,
            sort=sort,
        )[:size]

        if degraded and not merged:
            if self._metrics is not None:
                self._metrics.record_failure()
            raise SearchUnavailable("all_search_backends_failed")

        # Only an empty response needs these extra lookups: they let the
        # interface tell "the index has no documents yet" apart from "nothing
        # matched", and offer a corrected query when the index knows a closer
        # spelling (Meilisearch typo tolerance).
        local_index_empty = False
        query_suggestion: str | None = None
        if not merged:
            local_index_empty = await self._local.index_is_empty()
            if not local_index_empty:
                query_suggestion = await self._query_suggestion(query)

        local_hits = local_result.hits
        local_total = local_result.total if isinstance(local_result.total, int) else len(local_hits)

        if external_attempted:
            external_has_more = any(
                bool(result.has_more)
                or (result.has_more is None and len(result.hits) >= external_size)
                for _, result in provider_results
            )
            has_more = external_has_more or local_total > local_offset + len(local_hits)
            total = None
            total_pages = None
        else:
            total = local_total
            has_more = local_total > local_offset + len(local_hits)
            total_pages = math.ceil(local_total / size) if local_total else 0

        sources: list[ResultSource] = []
        for _, hit_source in merged:
            if hit_source not in sources:
                sources.append(hit_source)  # type: ignore[arg-type]

        external_sources = [source for source in sources if source != "local"]
        external_source = external_sources[0] if external_sources else None
        source: ResultSource = external_source or "local"
        external_hits = sum(1 for _, hit_source in merged if hit_source != "local")

        results = [
            SearchResultItem(
                id=stable_id(hit.url),
                title=hit.title,
                url=hit.url,
                snippet=hit.snippet,
                title_highlight=[
                    HighlightSegment(text=span.text, match=span.match)
                    for span in hit.title_highlight
                ]
                or None,
                snippet_highlight=[
                    HighlightSegment(text=span.text, match=span.match)
                    for span in hit.snippet_highlight
                ]
                or None,
                extra_snippets=list(hit.extra_snippets),
                source=hit_source,
                domain=hit.domain or domain_of(hit.url),
                h1=hit.h1 or None,
                rank=hit.rank,
                image=hit.image or None,
                crawled_at=hit.crawled_at or None,
            )
            for hit, hit_source in merged
        ]

        if self._metrics is not None:
            self._metrics.record_search(
                local_hits=len(local_hits),
                external_hits=external_hits,
                provider_errors=provider_errors,
                degraded=degraded,
                empty=not results,
            )

        return SearchResponse(
            query=query,
            engine=engine,
            source=source,
            sources=sources,
            available_providers=self.available_providers(),
            results=results,
            pagination=SearchPagination(
                page=page,
                page_size=size,
                has_more=has_more,
                total=total,
                total_pages=total_pages,
            ),
            meta=SearchMeta(
                took_ms=max(0, int((time.perf_counter() - started) * 1000)),
                local_hits=len(local_hits),
                brave_hits=provider_hits.get("brave", 0),
                external_hits=external_hits,
                provider_hits=provider_hits,
                provider_errors=list(dict.fromkeys(provider_errors)),
                local_index_empty=local_index_empty,
                query_suggestion=query_suggestion,
                insights=self._collect_insights(provider_results),
                degraded=degraded,
            ),
        )

    def _candidates(self) -> list[tuple[str, SearchProvider]]:
        candidates: list[tuple[str, SearchProvider]] = []
        if self._settings.brave_configured:
            candidates.append(("brave", self._brave))
        candidates.extend(
            (provider.name, provider)
            for provider in self._fallbacks
            if provider.configured
        )
        return candidates

    async def _search_fallback(
        self,
        candidates: Sequence[tuple[str, SearchProvider]],
        query: str,
        *,
        size: int,
        offset: int,
        page: int,
        local_keys: set[str],
    ) -> tuple[list[tuple[str, BackendResult]], dict[str, int], bool, list[str]]:
        """Try providers in order and stop at the first one adding a new URL."""

        provider_results: list[tuple[str, BackendResult]] = []
        provider_hits: dict[str, int] = {}
        provider_errors: list[str] = []
        degraded = False
        deadline = time.monotonic() + self._settings.external_budget_s

        for name, provider in candidates:
            # An open circuit means this provider failed repeatedly: skip it
            # instead of paying its timeout on every search. It is reported as
            # unavailable, so the interface can explain the partial answer.
            if not self._breaker.available(name):
                degraded = True
                provider_errors.append(name)
                logger.warning("%s skipped: circuit open", name)
                continue

            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                degraded = True
                logger.warning("External search budget exhausted before %s", name)
                break

            try:
                result = await asyncio.wait_for(
                    self._provider_call(
                        name,
                        provider,
                        query,
                        size=size,
                        offset=offset,
                        page=page,
                    ),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                degraded = True
                self._breaker.record_failure(name)
                provider_errors.append(name)
                logger.warning("%s fallback timed out", name)
                continue
            except BackendError as exc:
                degraded = True
                self._breaker.record_failure(name)
                provider_errors.append(name)
                logger.warning("%s fallback unavailable: %s", name, exc.reason)
                continue
            except Exception:  # noqa: BLE001 - one provider must not break the page
                degraded = True
                self._breaker.record_failure(name)
                provider_errors.append(name)
                logger.warning("%s fallback failed", name)
                continue

            self._breaker.record_success(name)
            provider_hits[name] = len(result.hits)

            if not result.hits:
                continue

            # A provider that only repeats local pages is not useful: keep
            # walking the shield until one adds at least one new URL.
            if any(url_key(hit.url) not in local_keys for hit in result.hits):
                provider_results.append((name, result))
                break

        return provider_results, provider_hits, degraded, provider_errors

    async def _search_parallel(
        self,
        candidates: Sequence[tuple[str, SearchProvider]],
        query: str,
        *,
        size: int,
        offset: int,
        page: int,
    ) -> tuple[list[tuple[str, BackendResult]], bool, list[str]]:
        """Query up to SEARCH_MAX_PROVIDERS together and merge their results."""

        provider_errors = [
            name for name, _ in candidates if not self._breaker.available(name)
        ]
        for name in provider_errors:
            logger.warning("%s skipped: circuit open", name)

        available = [
            (name, provider)
            for name, provider in candidates
            if self._breaker.available(name)
        ]
        limited = available[: self._settings.search_max_providers]
        if not limited:
            return [], bool(provider_errors), provider_errors

        tasks = {
            asyncio.create_task(
                self._provider_call(
                    name,
                    provider,
                    query,
                    size=size,
                    offset=offset,
                    page=page,
                )
            ): name
            for name, provider in limited
        }

        try:
            done, pending = await asyncio.wait(
                tasks.keys(),
                timeout=self._settings.external_budget_s,
            )
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        degraded = bool(pending) or bool(provider_errors)
        for task in pending:
            task.cancel()
            self._breaker.record_failure(tasks[task])
            provider_errors.append(tasks[task])
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        results: list[tuple[str, BackendResult]] = []
        for task in done:
            name = tasks[task]
            try:
                result = task.result()
            except BackendError as exc:
                degraded = True
                self._breaker.record_failure(name)
                provider_errors.append(name)
                logger.warning("%s fallback unavailable: %s", name, exc.reason)
                continue
            except asyncio.CancelledError:
                degraded = True
                self._breaker.record_failure(name)
                provider_errors.append(name)
                continue
            except Exception:  # noqa: BLE001 - one provider must not break the page
                degraded = True
                self._breaker.record_failure(name)
                provider_errors.append(name)
                logger.warning("%s fallback failed", name)
                continue
            self._breaker.record_success(name)
            results.append((name, result))

        order = {name: index for index, (name, _) in enumerate(limited)}
        results.sort(key=lambda item: order[item[0]])
        return results, degraded, provider_errors

    async def _provider_call(
        self,
        name: str,
        provider: SearchProvider,
        query: str,
        *,
        size: int,
        offset: int,
        page: int,
    ) -> BackendResult:
        """Call one provider through the short-lived cache.

        Only successful responses (including an empty one) are cached: a
        failure must be retried on the next search, not replayed from memory.
        """

        key = cache_key(name, query, page=page, size=size)
        if self._cache is not None:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        if name == "brave":
            # Brave's offset is expressed in pages, not results.
            result = await self._brave.search(query, count=size, offset=page - 1)
        else:
            result = await provider.search(query, count=size, offset=offset)

        if self._cache is not None:
            self._cache.put(key, result)
        return result

    async def _query_suggestion(self, query: str) -> str | None:
        """Best-effort "did you mean" for an empty result page.

        Each word is looked up on its own with ``matchingStrategy: "all"``:
        the highlighted runs contain the spelling stored in the index, and the
        closest one within the typo tolerance replaces the word. Quoted
        phrases are skipped: rewriting them would change the operator.

        Never raises and never stores anything: an unreachable index simply
        means no suggestion.
        """

        if '"' in query:
            return None

        terms = [
            term
            for term in re.findall(r"[^\W\d_]+", query, re.UNICODE)
            if len(term) >= _MIN_CORRECTION_TERM_LENGTH
        ][:_MAX_CORRECTION_TERMS]
        if not terms:
            return None

        corrections = await asyncio.gather(
            *(self._correct_term(term) for term in terms)
        )

        corrected = query
        changed = False
        for term, correction in zip(terms, corrections, strict=True):
            if correction is None:
                continue
            corrected = re.sub(
                rf"\b{re.escape(term)}\b",
                lambda _match: correction,
                corrected,
                count=1,
                flags=re.IGNORECASE,
            )
            changed = True

        if not changed or corrected == query:
            return None
        return corrected

    async def _correct_term(self, term: str) -> str | None:
        try:
            candidates = await self._local.matched_words(term, limit=5)
        except BackendError as exc:
            logger.warning("Local spelling lookup unavailable: %s", exc.reason)
            return None
        return closest_word(term, candidates, max_distance=tolerance_for(term))

    async def suggest(self, query: str, *, limit: int = 6) -> list[RawHit]:
        """Local prefix suggestions for the autocomplete dropdown.

        Never raises: an unreachable index means "no suggestions", not an
        error shown while the user is typing.
        """

        try:
            return await self._local.suggest(query, limit=limit)
        except BackendError as exc:
            logger.warning("Local suggestions unavailable: %s", exc.reason)
            return []

    async def local_available(self) -> bool:
        return await self._local.ping()

    def available_providers(self) -> list[str]:
        """External providers that have a key and can be selected by the frontend."""

        return [name for name, _ in self._candidates()]

    def provider_status(self) -> dict[str, str]:
        """Configured/disabled state for health reporting (never includes keys)."""

        statuses = {"brave": "configured" if self._settings.brave_configured else "disabled"}
        for provider in self._fallbacks:
            statuses[provider.name] = "configured" if provider.configured else "disabled"
        return statuses

    @staticmethod
    def _merge(
        local_hits: list[RawHit],
        provider_results: Sequence[tuple[str, BackendResult]],
    ) -> list[tuple[RawHit, str]]:
        """Local-first, then external results by provider weight and position.

        Each external hit scores ``weight(provider) - 0.03 * position``: the
        provider order still decides ties, but a fast provider cannot fill the
        page with weak results while a stronger provider's first hit waits.

        When the same URL arrives from multiple providers, the first hit keeps
        its source but receives the richest snippet and the union of the
        provider-specific extra excerpts.
        """

        merged: list[tuple[RawHit, str]] = []
        positions: dict[str, int] = {}

        def add(hit: RawHit, source: str) -> None:
            key = url_key(hit.url)
            if key in positions:
                index = positions[key]
                existing, existing_source = merged[index]
                merged[index] = (SearchService._enrich(existing, hit), existing_source)
                return
            positions[key] = len(merged)
            merged.append((hit, source))

        for hit in local_hits:
            add(hit, "local")

        external: list[tuple[float, int, int, RawHit, str]] = []
        for provider_index, (source, result) in enumerate(provider_results):
            weight = _PROVIDER_WEIGHTS.get(source, _PROVIDER_DEFAULT_WEIGHT)
            for position, hit in enumerate(result.hits):
                score = weight - _PROVIDER_POSITION_STEP * position
                external.append((score, provider_index, position, hit, source))

        external.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
        for _, _, _, hit, source in external:
            add(hit, source)

        return merged

    @staticmethod
    def _rerank_merged(
        merged: Sequence[tuple[RawHit, str]],
        query: str,
        *,
        sort: SearchSort,
    ) -> list[tuple[RawHit, str]]:
        """Reorder the local block in place, keeping local results before external ones.

        Local hits occupy the leading slots of the merge, so the rerank takes
        exactly those slots and writes the new order back: the local-first
        contract and the room reserved to providers are untouched. An explicit
        chronological sort is left alone, because the user asked for it.
        """

        ordered = list(merged)
        if sort != "relevance":
            return ordered

        positions = [index for index, (_, source) in enumerate(ordered) if source == "local"]
        if len(positions) < 2:
            return ordered

        reranked = rerank_local([ordered[index][0] for index in positions], query)
        for index, hit in zip(positions, reranked, strict=True):
            ordered[index] = (hit, "local")
        return ordered

    @staticmethod
    def _enrich(existing: RawHit, incoming: RawHit) -> RawHit:
        combined = tuple(
            dict.fromkeys((*existing.extra_snippets, *incoming.extra_snippets))
        )[:_MAX_EXTRA_SNIPPETS]
        snippet = existing.snippet
        snippet_highlight = existing.snippet_highlight
        if len(incoming.snippet) > len(snippet):
            snippet = incoming.snippet
            snippet_highlight = incoming.snippet_highlight
        title = existing.title
        title_highlight = existing.title_highlight
        if (not title or title == existing.domain) and incoming.title:
            title = incoming.title
            title_highlight = incoming.title_highlight
        return replace(
            existing,
            title=title,
            snippet=snippet,
            extra_snippets=combined,
            title_highlight=title_highlight,
            snippet_highlight=snippet_highlight,
        )

    @staticmethod
    def _collect_insights(
        provider_results: Sequence[tuple[str, BackendResult]],
    ) -> list[SearchInsight]:
        insights: list[SearchInsight] = []
        seen: set[tuple[str, str]] = set()
        for source, result in provider_results:
            for insight in result.insights:
                key = (insight.url, insight.text)
                if key in seen:
                    continue
                seen.add(key)
                insights.append(
                    SearchInsight(
                        source=source,  # type: ignore[arg-type]
                        title=insight.title,
                        text=insight.text,
                        url=insight.url or None,
                    )
                )
                if len(insights) >= _MAX_INSIGHTS:
                    return insights
        return insights
