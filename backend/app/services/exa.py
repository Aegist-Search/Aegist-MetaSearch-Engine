"""Server-side Exa Search fallback client.

Uses the official ``exa-py`` SDK (``AsyncExa``) as one step of the external
fallback chain. The API key stays on the server and exception messages contain
reason codes only, never the query or the key.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx
from exa_py import AsyncExa

from app.config import Settings
from app.services.errors import BackendError
from app.services.proxy_manager import ProxyEndpoint, ProxyManager
from app.services.results import BackendResult, RawHit, RawInsight
from app.services.text import clean_text
from app.utils.urls import domain_of, normalize_url

logger = logging.getLogger(__name__)

_MAX_RESULTS = 100
_STATUS_RE = re.compile(r"status code (\d{3})")


def _replace_http_clients(
    target: object,
    proxy: ProxyEndpoint,
) -> tuple[httpx.AsyncClient | None, list[httpx.AsyncClient]]:
    """Point every httpx client held by ``target`` to a proxied clone.

    ``exa-py`` exposes no proxy or transport option, so the SDK's internal
    ``httpx.AsyncClient`` is replaced. The replacement copies ``base_url``,
    headers (including the API key), timeout and redirect policy from the
    original: the SDK sends relative paths and relies on its configured
    headers, so a bare client would break authentication or routing.

    Attribute discovery (instead of a fixed private name) keeps this working
    across SDK releases, where the field has been called ``_client`` and is
    currently ``client``. Returns the new client and the replaced originals,
    both of which the caller must close.
    """

    replacement: httpx.AsyncClient | None = None
    replaced: list[httpx.AsyncClient] = []

    try:
        attributes = list(vars(target).items())
    except TypeError:
        # Slotted SDK object: no attribute to inspect, the caller falls back.
        return None, []

    for name, value in attributes:
        if not isinstance(value, httpx.AsyncClient):
            continue
        if replacement is None:
            replacement = httpx.AsyncClient(
                proxy=proxy.url,
                base_url=value.base_url,
                headers=value.headers,
                timeout=value.timeout,
                follow_redirects=value.follow_redirects,
                # The pool must not be bypassed by HTTP(S)_PROXY/ALL_PROXY.
                trust_env=False,
            )
        setattr(target, name, replacement)
        replaced.append(value)

    return replacement, replaced


class ExaClient:
    name = "exa"

    def __init__(
        self,
        settings: Settings,
        *,
        sdk: AsyncExa | None = None,
        proxies: ProxyManager | None = None,
    ) -> None:
        self._settings = settings
        self._proxies = proxies
        self._fallback_warned = False
        if sdk is not None:
            self._sdk: object | None = sdk
        elif settings.exa_configured:
            self._sdk = AsyncExa(api_key=settings.exa_api_key.strip())
        else:
            self._sdk = None

    @property
    def configured(self) -> bool:
        return self._settings.exa_configured

    async def search(self, query: str, *, count: int, offset: int) -> BackendResult:
        if not self.configured or self._sdk is None:
            return BackendResult()

        count = max(1, min(count, 20))
        offset = max(0, offset)
        # Exa has no result offset; fetch up to the end of the requested page
        # and slice locally. The public limit is 100 results.
        requested = min(_MAX_RESULTS, max(count, offset + count))

        contents: dict[str, object] = {
            "text": {"max_characters": 1_200},
            "highlights": True,
        }
        if self._settings.exa_include_summary:
            contents["summary"] = True

        try:
            response = await self._call_sdk(
                query,
                requested=requested,
                contents=contents,
            )
        except BackendError:
            raise
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            raise BackendError("exa_timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise BackendError(self._reason_from_status(exc.response.status_code)) from exc
        except httpx.HTTPError as exc:
            raise BackendError(f"exa_{type(exc).__name__.lower()}") from exc
        except Exception as exc:  # noqa: BLE001 - the shield must never crash the request
            raise BackendError(self._reason_from_error(exc)) from exc

        items = getattr(response, "results", None) or []
        hits: list[RawHit] = []
        insights: list[RawInsight] = []
        for item in items:
            hit = self._to_hit(item)
            if hit is not None:
                hits.append(hit)

            summary = clean_text(getattr(item, "summary", None), limit=800)
            if summary:
                raw_url = getattr(item, "url", None)
                url = normalize_url(raw_url if isinstance(raw_url, str) else "") or ""
                title = clean_text(getattr(item, "title", None), limit=200) or (
                    domain_of(url) if url else "Exa"
                )
                insights.append(RawInsight("exa", title, summary, url))

        if len(hits) > offset + count:
            has_more = True
        elif requested >= _MAX_RESULTS:
            # The public Exa limit has been reached: deeper pages are not
            # retrievable even if the current page is full.
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

    async def _call_sdk(
        self,
        query: str,
        *,
        requested: int,
        contents: dict[str, object],
    ) -> object:
        """Run ``search`` on the SDK, rotating a SOCKS5 proxy when enabled.

        A transport failure (refused tunnel, timeout, reset) quarantines the
        proxy and retries immediately with another one. HTTP errors returned
        by the API (401, 429, ...) are not transport failures: rotating would
        only burn quota, so they propagate unchanged.
        """

        if self._sdk is None:
            return None

        if self._proxies is None or not self._proxies.enabled:
            return await self._invoke(self._sdk, query, requested=requested, contents=contents)

        tried: set[ProxyEndpoint] = set()
        last_error: BaseException | None = None

        for _ in range(self._settings.proxy_max_attempts):
            endpoint = self._proxies.acquire_endpoint(exclude=tried)
            if endpoint is None:
                break
            tried.add(endpoint)

            sdk = AsyncExa(api_key=self._settings.exa_api_key.strip())
            try:
                replacement, replaced = _replace_http_clients(sdk, endpoint)
            except ImportError:
                if not self._fallback_warned:
                    logger.warning(
                        "SOCKS5 support unavailable (reason=proxy_socksio_missing)"
                    )
                self._warn_direct_fallback()
                break
            except ValueError:
                self._proxies.mark_failure(endpoint, "proxy_invalid_url")
                continue

            for orphan in replaced:
                await orphan.aclose()

            if replacement is None:
                # Unknown SDK layout: never let it silently bypass the proxy.
                if not self._fallback_warned:
                    logger.warning(
                        "Exa SDK proxy injection unsupported "
                        "(reason=exa_proxy_unsupported); using direct connection"
                    )
                self._warn_direct_fallback()
                break

            try:
                response = await self._invoke(
                    sdk,
                    query,
                    requested=requested,
                    contents=contents,
                )
            except (asyncio.TimeoutError, httpx.TransportError) as exc:
                self._proxies.mark_failure(endpoint, type(exc).__name__.lower())
                last_error = exc
                continue
            except BaseException:
                raise
            finally:
                await replacement.aclose()

            self._proxies.mark_success(endpoint)
            return response

        if last_error is not None and not self._settings.proxy_fallback_direct:
            raise last_error
        self._warn_direct_fallback()
        if last_error is None and not self._settings.proxy_fallback_direct:
            raise httpx.ProxyError("no_socks5_proxy_available")
        return await self._invoke(self._sdk, query, requested=requested, contents=contents)

    async def _invoke(
        self,
        sdk: object,
        query: str,
        *,
        requested: int,
        contents: dict[str, object],
    ) -> object:
        return await asyncio.wait_for(
            sdk.search(  # type: ignore[attr-defined]
                query,
                type=self._settings.exa_search_type,
                num_results=requested,
                moderation=self._settings.exa_moderation,
                contents=contents,
            ),
            timeout=self._settings.exa_timeout_s,
        )

    def _warn_direct_fallback(self) -> None:
        if self._fallback_warned:
            return
        self._fallback_warned = True
        # Reason code only: no SDK detail, query or proxy address.
        logger.warning("Exa SDK using direct server connection (reason=proxy_unavailable)")

    @staticmethod
    def _reason_from_error(exc: BaseException) -> str:
        match = _STATUS_RE.search(str(exc))
        if not match:
            return "exa_error"
        return ExaClient._reason_from_status(int(match.group(1)))

    @staticmethod
    def _reason_from_status(code: int) -> str:
        if code == 401:
            return "exa_unauthorized"
        if code == 403:
            return "exa_forbidden"
        if code == 429:
            return "exa_rate_limited"
        if code >= 500:
            return "exa_upstream_error"
        return f"exa_http_{code}"

    @staticmethod
    def _to_hit(item: object) -> RawHit | None:
        raw_url = getattr(item, "url", None)
        url = normalize_url(raw_url if isinstance(raw_url, str) else "")
        if not url:
            return None

        domain = domain_of(url)
        title = clean_text(getattr(item, "title", None), limit=300) or domain

        extra_snippets: list[str] = []
        raw_highlights = getattr(item, "highlights", None)
        if isinstance(raw_highlights, list):
            for raw in raw_highlights[:5]:
                cleaned = clean_text(raw, limit=300)
                if cleaned and cleaned not in extra_snippets:
                    extra_snippets.append(cleaned)

        snippet = clean_text(getattr(item, "text", None)) or (
            extra_snippets[0] if extra_snippets else ""
        )

        return RawHit(
            url=url,
            title=title,
            snippet=snippet,
            h1="",
            domain=domain,
            extra_snippets=tuple(extra_snippets),
        )
