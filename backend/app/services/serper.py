"""Server-side Serper (Google SERP) fallback client."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.services.errors import BackendError
from app.services.http_client import HttpRequester
from app.services.results import BackendResult, RawHit, RawInsight
from app.services.text import clean_text
from app.utils.urls import domain_of, normalize_url


class SerperClient:
    name = "serper"

    def __init__(self, http: HttpRequester, settings: Settings) -> None:
        self._http = http
        self._settings = settings
        self._base = settings.serper_base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return self._settings.serper_configured

    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        if not self.configured:
            return BackendResult()

        count = max(1, min(count, 20))
        offset = max(0, offset)
        page = offset // count + 1

        payload: dict[str, str | int] = {
            "q": query,
            "num": count,
            "page": page,
        }
        if self._settings.serper_gl.strip():
            payload["gl"] = self._settings.serper_gl.strip().lower()
        if self._settings.serper_hl.strip():
            payload["hl"] = self._settings.serper_hl.strip().lower()

        headers = {
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "User-Agent": self._settings.user_agent,
            "X-API-KEY": self._settings.serper_api_key.strip(),
        }

        try:
            response = await self._http.post(
                f"{self._base}/search",
                json=payload,
                headers=headers,
                timeout=self._settings.serper_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"serper_{type(exc).__name__.lower()}") from exc

        if response.status_code == 401:
            raise BackendError("serper_unauthorized")
        if response.status_code == 403:
            raise BackendError("serper_forbidden")
        if response.status_code == 429:
            raise BackendError("serper_rate_limited")
        if response.status_code >= 500:
            raise BackendError("serper_upstream_error")
        if response.status_code >= 400:
            raise BackendError(f"serper_http_{response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise BackendError("serper_invalid_json") from exc

        if not isinstance(data, dict):
            raise BackendError("serper_invalid_payload")

        items = data.get("organic")
        hits: list[RawHit] = []
        for item in items or []:
            if isinstance(item, dict):
                hit = self._to_hit(item)
                if hit is not None:
                    hits.append(hit)

        insights: list[RawInsight] = []
        answer_box = data.get("answerBox")
        if isinstance(answer_box, dict):
            text = clean_text(
                answer_box.get("answer") or answer_box.get("snippet"),
                limit=600,
            )
            if text:
                title = clean_text(answer_box.get("title"), limit=200) or "Risposta Serper"
                raw_url = answer_box.get("link") or ""
                url = normalize_url(raw_url if isinstance(raw_url, str) else "") or ""
                insights.append(RawInsight("serper", title, text, url))

        knowledge_graph = data.get("knowledgeGraph")
        if isinstance(knowledge_graph, dict):
            text = clean_text(knowledge_graph.get("description"), limit=600)
            if text and all(insight.text != text for insight in insights):
                title = clean_text(knowledge_graph.get("title"), limit=200) or "Knowledge Graph"
                raw_url = knowledge_graph.get("website") or knowledge_graph.get("descriptionLink") or ""
                url = normalize_url(raw_url if isinstance(raw_url, str) else "") or ""
                insights.append(RawInsight("serper", title, text, url))

        return BackendResult(
            hits=hits[:count],
            total=None,
            has_more=len(hits) >= count,
            insights=tuple(insights),
        )

    @staticmethod
    def _to_hit(item: dict) -> RawHit | None:
        raw_url = item.get("link")
        url = normalize_url(raw_url if isinstance(raw_url, str) else "")
        if not url:
            return None

        domain = domain_of(url)
        title = clean_text(item.get("title"), limit=300) or domain
        snippet = clean_text(item.get("snippet"))

        return RawHit(
            url=url,
            title=title,
            snippet=snippet,
            h1="",
            domain=domain,
        )
