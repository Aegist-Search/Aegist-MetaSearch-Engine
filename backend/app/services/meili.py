"""Async Meilisearch REST client used by the search API."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote

import httpx

from app.config import Settings
from app.services.errors import BackendError
from app.services.results import BackendResult, RawHit
from app.services.spelling import words_in_spans
from app.utils.highlight import HighlightSpan, parse_highlighted
from app.utils.urls import domain_of, normalize_url, url_key

_RETRIEVED_ATTRIBUTES = [
    "id",
    "title",
    "url",
    "snippet",
    "h1",
    "domain",
    "image",
    "crawled_at",
]

# Explicit tags: the parser and the React renderer do not depend on the
# Meilisearch defaults, which an admin could change on the instance.
_HIGHLIGHT_PRE_TAG = "<em>"
_HIGHLIGHT_POST_TAG = "</em>"
_HIGHLIGHT_ATTRIBUTES = ["title", "snippet"]


def _highlight_for(value: str | None, plain: str) -> tuple[HighlightSpan, ...]:
    """Keep the highlighted value only when it still matches the plain field.

    The frontend renders the segments *instead of* the plain string, so a
    mismatch (for example a cropped formatted value) would show text that is
    not in the result.
    """

    spans = parse_highlighted(value)
    if not spans:
        return ()
    if "".join(span.text for span in spans).strip() != plain:
        return ()
    return spans


class MeilisearchClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._settings = settings
        self._base = settings.meili_url.rstrip("/")
        self._index_path = f"/indexes/{quote(settings.meili_index, safe='')}"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        key = self._settings.meili_master_key.strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def search(
        self,
        query: str,
        *,
        limit: int,
        offset: int,
        sort: Sequence[str] | None = None,
        filter: str | None = None,
    ) -> BackendResult:
        payload: dict[str, object] = {
            "q": query,
            "limit": limit,
            "offset": offset,
            "attributesToRetrieve": _RETRIEVED_ATTRIBUTES,
            "attributesToHighlight": _HIGHLIGHT_ATTRIBUTES,
            "highlightPreTag": _HIGHLIGHT_PRE_TAG,
            "highlightPostTag": _HIGHLIGHT_POST_TAG,
            "showRankingScore": True,
            "matchingStrategy": "last",
        }
        # Meilisearch only applies a sort expression that is listed in
        # ``sortableAttributes``: ``crawled_at`` is, see index_config.py.
        if sort:
            payload["sort"] = list(sort)
        # Built by app.utils.query from validated hostnames/language codes:
        # no user string is ever interpolated into the filter verbatim.
        if filter:
            payload["filter"] = filter

        try:
            response = await self._http.post(
                f"{self._base}{self._index_path}/search",
                json=payload,
                headers=self._headers(),
                timeout=self._settings.meili_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"meilisearch_{type(exc).__name__.lower()}") from exc

        if response.status_code == 404 and self._is_missing_index(response):
            return BackendResult(hits=[], total=0)

        if response.status_code >= 400:
            raise BackendError(f"meilisearch_http_{response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise BackendError("meilisearch_invalid_json") from exc

        if not isinstance(data, dict):
            raise BackendError("meilisearch_invalid_payload")

        hits: list[RawHit] = []
        for item in data.get("hits") or []:
            if isinstance(item, dict):
                hit = self._to_hit(item)
                if hit is not None:
                    hits.append(hit)

        total = data.get("estimatedTotalHits")
        if not isinstance(total, int):
            total = data.get("totalHits")
        if not isinstance(total, int):
            total = len(hits)

        return BackendResult(hits=hits, total=total)

    async def suggest(self, query: str, *, limit: int = 6) -> list[RawHit]:
        """Prefix search on title/domain for the autocomplete dropdown.

        Deliberately local-only: the endpoint behind this method must never
        call an external provider, so a keystroke cannot leak the query.
        """

        payload = {
            "q": query,
            "limit": limit,
            "attributesToRetrieve": ["id", "title", "url", "domain"],
            "attributesToSearchOn": ["title", "domain"],
            "matchingStrategy": "last",
        }

        try:
            response = await self._http.post(
                f"{self._base}{self._index_path}/search",
                json=payload,
                headers=self._headers(),
                timeout=self._settings.meili_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"meilisearch_{type(exc).__name__.lower()}") from exc

        if response.status_code == 404 and self._is_missing_index(response):
            return []
        if response.status_code >= 400:
            raise BackendError(f"meilisearch_http_{response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise BackendError("meilisearch_invalid_json") from exc
        if not isinstance(data, dict):
            raise BackendError("meilisearch_invalid_payload")

        hits: list[RawHit] = []
        seen: set[str] = set()
        for item in data.get("hits") or []:
            if not isinstance(item, dict):
                continue
            hit = self._to_hit(item)
            if hit is None:
                continue
            key = url_key(hit.url)
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)
        return hits

    async def matched_words(self, term: str, *, limit: int = 5) -> list[str]:
        """Index spellings that Meilisearch matched for a possibly wrong term.

        A single-term full-text search relies on the index typo tolerance; the
        highlighted runs of the returned titles/snippets contain the spelling
        actually stored, which is what the caller compares with ``term``.
        ``matchingStrategy: "all"`` is deliberate: the term must match as a
        whole, without the word-dropping relaxation used by the page search.
        """

        payload = {
            "q": term,
            "limit": limit,
            "attributesToRetrieve": ["id"],
            "attributesToHighlight": ["title", "snippet"],
            "attributesToSearchOn": ["title", "snippet"],
            "highlightPreTag": _HIGHLIGHT_PRE_TAG,
            "highlightPostTag": _HIGHLIGHT_POST_TAG,
            "matchingStrategy": "all",
        }

        try:
            response = await self._http.post(
                f"{self._base}{self._index_path}/search",
                json=payload,
                headers=self._headers(),
                timeout=self._settings.meili_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"meilisearch_{type(exc).__name__.lower()}") from exc

        if response.status_code == 404 and self._is_missing_index(response):
            return []
        if response.status_code >= 400:
            raise BackendError(f"meilisearch_http_{response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise BackendError("meilisearch_invalid_json") from exc
        if not isinstance(data, dict):
            raise BackendError("meilisearch_invalid_payload")

        words: list[str] = []
        for item in data.get("hits") or []:
            if not isinstance(item, dict):
                continue
            formatted = item.get("_formatted")
            if not isinstance(formatted, dict):
                continue
            for field in ("title", "snippet"):
                spans = parse_highlighted(formatted.get(field))
                words.extend(words_in_spans(spans))

        return list(dict.fromkeys(words))

    async def index_is_empty(self) -> bool:
        """Whether the index holds no documents (used only for empty pages).

        A missing index counts as empty: that is the state of a fresh install.
        Any other error returns ``False``, because "unknown" must not be shown
        as "the index is empty".
        """

        try:
            response = await self._http.get(
                f"{self._base}{self._index_path}/stats",
                headers=self._headers(),
                timeout=self._settings.meili_timeout_s,
            )
        except httpx.HTTPError:
            return False

        if response.status_code == 404:
            return True
        if response.status_code >= 400:
            return False

        try:
            data = response.json()
        except ValueError:
            return False
        if not isinstance(data, dict):
            return False

        count = data.get("numberOfDocuments")
        return isinstance(count, int) and count == 0

    async def ping(self) -> bool:
        try:
            response = await self._http.get(f"{self._base}/health", timeout=2.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    @staticmethod
    def _is_missing_index(response: httpx.Response) -> bool:
        try:
            payload = response.json()
        except ValueError:
            return False
        return isinstance(payload, dict) and payload.get("code") == "index_not_found"

    @staticmethod
    def _to_hit(item: dict) -> RawHit | None:
        url = normalize_url(item.get("url") if isinstance(item.get("url"), str) else "")
        if not url:
            return None

        domain = item.get("domain")
        if not isinstance(domain, str) or not domain:
            domain = domain_of(url)

        title = item.get("title")
        title = title.strip() if isinstance(title, str) and title.strip() else domain

        snippet = item.get("snippet")
        snippet = snippet.strip() if isinstance(snippet, str) else ""

        h1 = item.get("h1")
        h1 = h1.strip() if isinstance(h1, str) else ""

        image = item.get("image")
        image = image.strip() if isinstance(image, str) else ""

        rank = item.get("_rankingScore")
        rank = float(rank) if isinstance(rank, (int, float)) else None

        crawled_at = item.get("crawled_at")
        if isinstance(crawled_at, bool):
            crawled_at = 0
        elif isinstance(crawled_at, (int, float)):
            crawled_at = int(crawled_at)
        else:
            crawled_at = 0

        formatted = item.get("_formatted")
        formatted = formatted if isinstance(formatted, dict) else {}

        return RawHit(
            url=url,
            title=title,
            snippet=snippet,
            h1=h1,
            domain=domain,
            rank=rank,
            image=image,
            crawled_at=max(0, crawled_at),
            title_highlight=_highlight_for(formatted.get("title"), title),
            snippet_highlight=_highlight_for(formatted.get("snippet"), snippet),
        )
