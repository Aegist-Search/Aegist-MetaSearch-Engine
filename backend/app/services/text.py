"""Small, dependency-free helpers shared by external search providers."""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]*>")


def clean_text(value: object, limit: int = 420) -> str:
    """Strip HTML tags, collapse whitespace and truncate on a word boundary."""

    if not isinstance(value, str):
        return ""

    text = html.unescape(_TAG_RE.sub("", value))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit]
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
        text = cut.rstrip(" ,;:.!?-") + "…"
    return text
