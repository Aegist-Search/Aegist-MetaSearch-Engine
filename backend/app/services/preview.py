"""Server-side image proxy used by the result preview.

The browser never contacts the origin site: the backend downloads the preview
image, keeps it in a bounded in-memory cache and serves the bytes. That keeps
the privacy promise intact (no IP leak, no third-party screenshot service).

Because the endpoint fetches arbitrary URLs it must not become an SSRF pivot:

* only ``http``/``https`` URLs are accepted;
* every hop (including redirect targets) must resolve to public addresses;
* the validated IP is pinned for the connection (the hostname survives only
  in ``Host`` and TLS SNI), so a second DNS answer cannot redirect the fetch
  to an internal address (DNS rebinding);
* redirects are followed manually, at most ``MAX_REDIRECTS`` times;
* only raster image types are accepted and the body is capped in size.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.utils.net import PublicTarget, is_public_url, resolve_public_target

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_REDIRECTS = 2
MAX_URL_LENGTH = 2048
CACHE_LIMIT_BYTES = 32 * 1024 * 1024

# Raster formats only: an inline SVG could carry script when opened directly.
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/avif",
        "image/bmp",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }
)

_ACCEPT_HEADER = "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8"


@dataclass(frozen=True, slots=True)
class PreviewImage:
    content: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class _Redirect:
    location: str


def _clean_url(raw: str) -> str:
    """Return a trimmed http(s) URL, or an empty string when unusable."""

    candidate = raw.strip()
    if not candidate or len(candidate) > MAX_URL_LENGTH:
        return ""
    if any(character.isspace() for character in candidate):
        return ""

    try:
        parsed = urlsplit(candidate)
        # ``port`` raises on malformed authorities; touch it to validate the URL.
        parsed.port
    except ValueError:
        return ""

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return candidate


async def is_public_image_url(url: str) -> bool:
    """True only when every address behind ``url`` is public.

    Kept as a named alias for the preview proxy: the shared SSRF guard in
    ``app.utils.net`` is also used by the on-demand crawler.
    """

    return await is_public_url(url)


class PreviewService:
    """Downloads preview images once and serves them from memory."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout_s: float = 8.0,
        cache_limit_bytes: int = CACHE_LIMIT_BYTES,
    ) -> None:
        self._http = http
        self._timeout = timeout_s
        self._cache_limit = max(0, cache_limit_bytes)
        self._cache: OrderedDict[str, PreviewImage] = OrderedDict()
        self._cached_bytes = 0

    async def fetch(self, raw_url: str) -> PreviewImage | None:
        url = _clean_url(raw_url)
        if not url:
            return None

        cached = self._cache.get(url)
        if cached is not None:
            self._cache.move_to_end(url)
            return cached

        image = await self._download(url)
        if image is not None:
            self._store(url, image)
        return image

    async def _download(self, url: str) -> PreviewImage | None:
        target_url = url

        for _hop in range(MAX_REDIRECTS + 1):
            target = await resolve_public_target(target_url)
            if target is None:
                logger.warning("Preview rejected a non-public image host")
                return None

            outcome = await self._request_pinned(target)
            if isinstance(outcome, _Redirect):
                target_url = urljoin(target.url, outcome.location)
                continue
            return outcome

        return None

    async def _request_pinned(self, target: PublicTarget) -> PreviewImage | _Redirect | None:
        """Fetch ``target`` using only the addresses validated before the request."""

        for address in target.addresses:
            try:
                return await self._request_once(target, address)
            except httpx.HTTPError as exc:
                # A dead address or a dropped tunnel: another validated address
                # may still work, and none of them requires a new DNS lookup.
                logger.warning("Preview download failed (%s)", type(exc).__name__)
        return None

    async def _request_once(
        self,
        target: PublicTarget,
        address: str,
    ) -> PreviewImage | _Redirect | None:
        headers = {
            "Accept": _ACCEPT_HEADER,
            "User-Agent": "AegisSearchPreview/1.0",
            "Host": target.host_header,
        }
        extensions = {"sni_hostname": target.host} if target.secure else None

        async with self._http.stream(
            "GET",
            target.url_for(address),
            headers=headers,
            timeout=self._timeout,
            follow_redirects=False,
            extensions=extensions,
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return None
                return _Redirect(location)

            if response.status_code != 200:
                logger.warning("Preview rejected: origin returned HTTP %s", response.status_code)
                return None

            content_type = (
                (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            )
            if content_type not in ALLOWED_CONTENT_TYPES:
                logger.warning("Preview rejected: unsupported content type")
                return None

            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
                logger.warning("Preview rejected: image larger than the size cap")
                return None

            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if len(buffer) > MAX_IMAGE_BYTES:
                    logger.warning("Preview rejected: image larger than the size cap")
                    return None

            if not buffer:
                logger.warning("Preview rejected: empty image body")
                return None
            return PreviewImage(content=bytes(buffer), content_type=content_type)

    def _store(self, url: str, image: PreviewImage) -> None:
        size = len(image.content)
        if size > self._cache_limit:
            return

        self._cache[url] = image
        self._cached_bytes += size
        while self._cached_bytes > self._cache_limit and self._cache:
            _, evicted = self._cache.popitem(last=False)
            self._cached_bytes -= len(evicted.content)
