"""Server-side Brave Search fallback client.

The API key never leaves the backend, no cookies are sent and query strings are
kept out of every exception/log message.
"""

from __future__ import annotations

import httpx

from app.config import Settings
from app.services.errors import BackendError
from app.services.http_client import HttpRequester
from app.services.results import BackendResult, RawHit, RawInsight
from app.services.text import clean_text
from app.utils.urls import domain_of, normalize_url


class BraveClient:
    name = "brave"

    def __init__(self, http: HttpRequester, settings: Settings) -> None:
        self._http = http
        self._settings = settings
        self._base = settings.brave_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return self._settings.brave_configured

    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        if not self.configured:
            return BackendResult()

        params: dict[str, str | int] = {
            "q": query,
            "count": max(1, min(count, 20)),
            "offset": max(0, min(offset, 9)),
            "safesearch": self._settings.brave_safesearch,
            "text_decorations": "false",
            "spellcheck": "false",
            "result_filter": "web",
            "extra_snippets": "true" if self._settings.brave_extra_snippets else "false",
        }
        if self._settings.brave_country.strip():
            params["country"] = self._settings.brave_country.strip().upper()
        if self._settings.brave_search_lang.strip():
            params["search_lang"] = self._settings.brave_search_lang.strip().lower()
        if self._settings.brave_ui_lang.strip():
            params["ui_lang"] = self._settings.brave_ui_lang.strip()

        headers = {
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": self._settings.user_agent,
            "X-Subscription-Token": self._settings.brave_api_key.strip(),
        }

        try:
            response = await self._http.get(
                f"{self._base}/web/search",
                params=params,
                headers=headers,
                timeout=self._settings.brave_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"brave_{type(exc).__name__.lower()}") from exc

        if response.status_code == 401:
            raise BackendError("brave_unauthorized")
        if response.status_code == 403:
            raise BackendError("brave_forbidden")
        if response.status_code == 429:
            raise BackendError("brave_rate_limited")
        if response.status_code >= 500:
            raise BackendError("brave_upstream_error")
        if response.status_code >= 400:
            raise BackendError(f"brave_http_{response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise BackendError("brave_invalid_json") from exc

        if not isinstance(data, dict):
            raise BackendError("brave_invalid_payload")

        web = data.get("web")
        items = web.get("results") if isinstance(web, dict) else None
        hits: list[RawHit] = []
        for item in items or []:
            if isinstance(item, dict):
                hit = self._to_hit(item)
                if hit is not None:
                    hits.append(hit)

        has_more: bool | None = None
        query_meta = data.get("query")
        if isinstance(query_meta, dict):
            flag = query_meta.get("more_results_available")
            if isinstance(flag, bool):
                has_more = flag

        insights: list[RawInsight] = []
        infobox = data.get("infobox")
        if isinstance(infobox, dict):
            text = clean_text(
                infobox.get("long_desc") or infobox.get("description"),
                limit=600,
            )
            if text:
                title = clean_text(infobox.get("title"), limit=200) or "Brave"
                raw_url = infobox.get("website") or infobox.get("url") or ""
                url = normalize_url(raw_url if isinstance(raw_url, str) else "") or ""
                insights.append(RawInsight("brave", title, text, url))

        return BackendResult(hits=hits, total=None, has_more=has_more, insights=tuple(insights))

    @staticmethod
    def _to_hit(item: dict) -> RawHit | None:
        raw_url = item.get("url")
        url = normalize_url(raw_url if isinstance(raw_url, str) else "")
        if not url:
            return None

        domain = domain_of(url)
        title = clean_text(item.get("title"), limit=300) or domain

        extra_snippets: list[str] = []
        raw_extras = item.get("extra_snippets")
        if isinstance(raw_extras, list):
            for raw in raw_extras[:5]:
                cleaned = clean_text(raw, limit=300)
                if cleaned:
                    extra_snippets.append(cleaned)

        snippet = clean_text(item.get("description")) or (extra_snippets[0] if extra_snippets else "")

        return RawHit(
            url=url,
            title=title,
            snippet=snippet,
            h1="",
            domain=domain,
            extra_snippets=tuple(extra_snippets),
        )
