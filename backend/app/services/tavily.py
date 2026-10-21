"""Server-side Tavily Search fallback client."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.services.errors import BackendError
from app.services.http_client import HttpRequester
from app.services.results import BackendResult, RawHit, RawInsight
from app.services.text import clean_text
from app.utils.urls import domain_of, normalize_url

_MAX_RESULTS = 20


class TavilyClient:
    name = "tavily"

    def __init__(self, http: HttpRequester, settings: Settings) -> None:
        self._http = http
        self._settings = settings
        self._base = settings.tavily_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return self._settings.tavily_configured

    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        if not self.configured:
            return BackendResult()

        count = max(1, min(count, 20))
        offset = max(0, offset)
        # Tavily has no result offset and caps max_results at 20; fetch up to
        # the end of the page and slice locally.
        requested = min(_MAX_RESULTS, max(count, offset + count))

        payload: dict[str, object] = {
            "query": query,
            "search_depth": self._settings.tavily_search_depth,
            "max_results": requested,
            "topic": self._settings.tavily_topic,
            "include_answer": self._settings.tavily_include_answer,
            "include_raw_content": self._settings.tavily_include_raw_content,
            "include_images": False,
            "include_favicon": False,
            "safe_search": self._settings.tavily_safe_search,
        }
        if self._settings.tavily_language.strip():
            payload["language"] = self._settings.tavily_language.strip().lower()

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._settings.tavily_api_key.strip()}",
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "User-Agent": self._settings.user_agent,
        }

        try:
            response = await self._http.post(
                f"{self._base}/search",
                json=payload,
                headers=headers,
                timeout=self._settings.tavily_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"tavily_{type(exc).__name__.lower()}") from exc

        if response.status_code == 401:
            raise BackendError("tavily_unauthorized")
        if response.status_code == 403:
            raise BackendError("tavily_forbidden")
        if response.status_code == 429:
            raise BackendError("tavily_rate_limited")
        if response.status_code == 432:
            raise BackendError("tavily_plan_limit")
        if response.status_code >= 500:
            raise BackendError("tavily_upstream_error")
        if response.status_code >= 400:
            raise BackendError(f"tavily_http_{response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise BackendError("tavily_invalid_json") from exc

        if not isinstance(data, dict):
            raise BackendError("tavily_invalid_payload")

        items = data.get("results")
        hits: list[RawHit] = []
        for item in items or []:
            if isinstance(item, dict):
                hit = self._to_hit(
                    item,
                    include_raw_content=self._settings.tavily_include_raw_content,
                )
                if hit is not None:
                    hits.append(hit)

        insights: list[RawInsight] = []
        answer = clean_text(data.get("answer"), limit=800)
        if answer:
            insights.append(RawInsight("tavily", "Risposta Tavily", answer))

        if len(hits) > offset + count:
            has_more = True
        elif requested >= _MAX_RESULTS:
            # Tavily caps max_results at 20: pages beyond the cap cannot be
            # retrieved, so stop paginating instead of showing empty pages.
            has_more = False
        elif len(hits) == requested:
            has_more = True
        else:
            has_more = False

        return BackendResult(
            hits=hits[offset : offset + count],
            total=None,
            has_more=has_more,
            insights=tuple(insights),
        )

    @staticmethod
    def _to_hit(item: dict, *, include_raw_content: bool = False) -> RawHit | None:
        raw_url = item.get("url")
        url = normalize_url(raw_url if isinstance(raw_url, str) else "")
        if not url:
            return None

        domain = domain_of(url)
        title = clean_text(item.get("title"), limit=300) or domain
        snippet = clean_text(item.get("content"))

        extra_snippets: list[str] = []
        if include_raw_content:
            raw_content = clean_text(item.get("raw_content"), limit=600)
            if raw_content and raw_content != snippet:
                extra_snippets.append(raw_content)

        return RawHit(
            url=url,
            title=title,
            snippet=snippet,
            h1="",
            domain=domain,
            extra_snippets=tuple(extra_snippets),
        )
