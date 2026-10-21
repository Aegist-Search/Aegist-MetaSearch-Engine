"""URL helpers shared by ingestion and the search API."""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "yclid",
    "twclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
    "_ga",
    "_gl",
    "mkt_tok",
    "vero_conv",
    "vero_id",
}


def _strip_tracking(query: str) -> str:
    if not query:
        return ""
    pairs = parse_qsl(query, keep_blank_values=True)
    kept = [
        (key, value)
        for key, value in pairs
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    return urlencode(kept, doseq=True)


def normalize_url(raw: str | None) -> str | None:
    """Return a canonical http(s) URL or ``None`` when it is not usable.

    - lowercases scheme and host, drops userinfo
    - removes default ports, fragments and common tracking parameters
    - removes a trailing slash on non-root paths
    """

    value = (raw or "").strip()
    if not value:
        return None

    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        return None

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"

    netloc = host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    query = _strip_tracking(parts.query)
    return urlunsplit((scheme, netloc, path, query, ""))


def url_key(url: str) -> str:
    """Scheme/``www``-insensitive key used to deduplicate results."""

    normalized = normalize_url(url) or url.strip()
    try:
        parts = urlsplit(normalized)
        host = (parts.hostname or "").lower()
        path = (parts.path or "/").rstrip("/").lower() or "/"
        query = _strip_tracking(parts.query)
    except ValueError:
        return normalized.lower()

    if host.startswith("www."):
        host = host[4:]
    return f"{host}{path}" + (f"?{query}" if query else "")


def domain_of(url: str) -> str:
    """Return a display-friendly hostname (without ``www.``)."""

    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def stable_id(url: str) -> str:
    """Deterministic 40-char id, also used as Meilisearch primary key."""

    canonical = normalize_url(url) or url
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()
