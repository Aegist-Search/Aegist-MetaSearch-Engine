"""Parse Katana JSONL output and turn pages into Meilisearch documents.

Katana (``-jsonl``, body included by default) writes one JSON object per line:

    {"timestamp": "...",
     "request": {"method": "GET", "endpoint": "https://example.com/a", ...},
     "response": {"status_code": 200, "content_type": "text/html", "body": "<html>..."}}

This module never raises on a single bad page: it skips it and keeps going.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.utils.urls import domain_of, normalize_url, stable_id

logger = logging.getLogger(__name__)

CONTENT_MAX_CHARS = 20_000
SNIPPET_MAX_CHARS = 320
TITLE_MAX_CHARS = 300
MIN_DESCRIPTION_CHARS = 40

_REMOVED_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "canvas", "select", "button")

# Meta tags that carry the social preview image, best candidate first.
_IMAGE_META_NAMES = [
    "og:image:secure_url",
    "og:image:url",
    "og:image",
    "twitter:image",
    "twitter:image:src",
]


class KatanaFormatError(RuntimeError):
    """The input file is not readable as Katana JSON/JSONL."""


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-") + "…"


def _meta_content(soup: BeautifulSoup, names: list[str]) -> str:
    for name in names:
        pattern = re.compile(rf"^{re.escape(name)}$", re.IGNORECASE)
        tag = soup.find("meta", attrs={"name": pattern}) or soup.find("meta", attrs={"property": pattern})
        if tag is not None:
            content = _text(tag.get("content"))
            if content:
                return content
    return ""


def _first_h1(soup: BeautifulSoup) -> str:
    heading = soup.find("h1")
    if heading is None:
        return ""
    return _truncate(_text(heading.get_text(" ", strip=True)), TITLE_MAX_CHARS)


def _image_url(soup: BeautifulSoup, page_url: str) -> str:
    """Absolute http(s) URL of the page preview image, or an empty string."""

    candidate = _meta_content(soup, _IMAGE_META_NAMES)
    if not candidate:
        return ""

    absolute = urljoin(page_url, candidate)
    parsed = urlsplit(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return absolute


def _title_from_url(url: str) -> str:
    path = unquote(urlsplit(url).path).rstrip("/")
    segment = path.rsplit("/", 1)[-1] if path else ""
    if segment:
        cleaned = re.sub(r"\.(?:html?|php|aspx?|jsp)$", "", segment, flags=re.IGNORECASE)
        cleaned = re.sub(r"[-_]+", " ", cleaned).strip()
        if cleaned:
            return _truncate(cleaned.title(), TITLE_MAX_CHARS)
    return domain_of(url)


def content_hash(
    title: str,
    h1: str,
    snippet: str,
    content: str,
    image: str,
    lang: str,
) -> str:
    """Stable fingerprint of the indexed fields, used for incremental ingestion.

    Only fields that end up in the index take part: a change in the raw HTML
    (a new nonce, a timestamp in the footer) that does not change the extracted
    text must not force an upload. ``crawled_at`` is deliberately excluded:
    every crawl updates it, so including it would make every document look
    changed.
    """

    payload = "\x1f".join((title, h1, snippet, content, image, lang))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _parse_timestamp(value: object) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return int(time.time())


def _content_type(record: Mapping[str, Any], response: Mapping[str, Any]) -> str:
    raw: object = response.get("content_type") or record.get("content_type")
    if not raw:
        headers = response.get("headers")
        if isinstance(headers, Mapping):
            for key, value in headers.items():
                if str(key).lower() == "content-type":
                    if isinstance(value, (list, tuple)) and value:
                        raw = value[0]
                    else:
                        raw = value
                    break
    return str(raw or "").lower().split(";", 1)[0].strip()


def extract_document(
    record: Mapping[str, Any],
    *,
    require_body: bool = True,
    crawled_at_fallback: int | None = None,
) -> dict[str, Any] | None:
    """Turn a single Katana record into an index document.

    Returns ``None`` for non-HTML resources, non-2xx responses and events
    without a usable body.
    """

    request = record.get("request")
    request = request if isinstance(request, Mapping) else {}
    response = record.get("response")
    response = response if isinstance(response, Mapping) else {}

    raw_url = request.get("endpoint") or request.get("url") or record.get("url")
    url = normalize_url(raw_url if isinstance(raw_url, str) else "")
    if not url:
        return None

    raw_status = response.get("status_code", record.get("status_code"))
    try:
        status_code = int(raw_status) if raw_status is not None else 200
    except (TypeError, ValueError):
        status_code = 200
    if not 200 <= status_code < 300:
        return None

    raw_body = response.get("body") or record.get("body") or ""
    body = raw_body if isinstance(raw_body, str) else ""
    content_type = _content_type(record, response)

    if not body.strip():
        if require_body:
            return None
        soup = BeautifulSoup("", "html.parser")
    else:
        if content_type and "html" not in content_type:
            return None
        if not content_type and not body.lstrip().startswith("<"):
            return None
        try:
            soup = BeautifulSoup(body, "html.parser")
        except Exception:  # beautifulsoup is defensive, but never fail the batch
            logger.warning("Unparseable HTML for %s", domain_of(url))
            return None

    html_tag = soup.find("html")
    lang = ""
    if html_tag is not None and isinstance(html_tag.get("lang"), str):
        lang = html_tag["lang"].strip().lower().split("-", 1)[0][:8]

    h1 = _first_h1(soup)
    description = _meta_content(soup, ["description", "og:description", "twitter:description"])

    title = ""
    for candidate in (
        _meta_content(soup, ["og:title", "twitter:title"]),
        _text(soup.title.get_text(" ", strip=True)) if soup.title else "",
        h1,
    ):
        if candidate:
            title = _truncate(candidate, TITLE_MAX_CHARS)
            break
    if not title:
        title = _title_from_url(url)

    for tag in soup(_REMOVED_TAGS):
        tag.decompose()
    content = _truncate(_text(soup.get_text(" ", strip=True)), CONTENT_MAX_CHARS)

    if len(description) >= MIN_DESCRIPTION_CHARS:
        snippet = _truncate(description, SNIPPET_MAX_CHARS)
    else:
        snippet = _truncate(content or description, SNIPPET_MAX_CHARS)

    crawled_at = _parse_timestamp(response.get("timestamp") or record.get("timestamp"))
    if crawled_at_fallback is not None and crawled_at <= 0:
        crawled_at = crawled_at_fallback

    image = _image_url(soup, url)

    return {
        "id": stable_id(url),
        "url": url,
        "title": title,
        "h1": h1,
        "image": image,
        "snippet": snippet,
        "content": content,
        "content_hash": content_hash(title, h1, snippet, content, image, lang),
        "domain": domain_of(url),
        "lang": lang,
        "status_code": status_code,
        "crawled_at": crawled_at,
        "source": "katana",
    }


def _load_array(handle: Any, path: Path) -> Iterator[dict[str, Any]]:
    try:
        payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise KatanaFormatError(f"Invalid JSON array in {path.name} (line {exc.lineno})") from exc
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
    elif isinstance(payload, dict):
        yield payload
    else:
        raise KatanaFormatError(f"Unexpected top-level JSON type in {path.name}")


def iter_katana_records(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield records from a Katana JSONL file (or a single JSON array/object)."""

    file_path = Path(path)
    with file_path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        first_line = ""
        for line in handle:
            if line.strip():
                first_line = line
                break
        if not first_line:
            return

        stripped = first_line.strip()
        if stripped.startswith("["):
            handle.seek(0)
            yield from _load_array(handle, file_path)
            return

        try:
            first_item = json.loads(stripped)
        except json.JSONDecodeError:
            # A pretty-printed single object/array, not JSONL.
            handle.seek(0)
            yield from _load_array(handle, file_path)
            return

        if isinstance(first_item, dict):
            yield first_item

        for line_number, line in enumerate(handle, start=2):
            candidate = line.strip()
            if not candidate or candidate in {"[", "]"}:
                continue
            if candidate.endswith(","):
                candidate = candidate[:-1]
            try:
                item = json.loads(candidate)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSON at %s:%d", file_path.name, line_number)
                continue
            if isinstance(item, dict):
                yield item
