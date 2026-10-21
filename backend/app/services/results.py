"""Shape of the intermediate results returned by search backends."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.utils.highlight import HighlightSpan


@dataclass(frozen=True, slots=True)
class RawHit:
    url: str
    title: str
    snippet: str = ""
    h1: str = ""
    domain: str = ""
    rank: float | None = None
    extra_snippets: tuple[str, ...] = ()
    image: str = ""
    crawled_at: int = 0
    title_highlight: tuple[HighlightSpan, ...] = ()
    snippet_highlight: tuple[HighlightSpan, ...] = ()


@dataclass(frozen=True, slots=True)
class RawInsight:
    """Provider-specific answer or knowledge snippet shown above the results."""

    source: str
    title: str
    text: str
    url: str = ""


@dataclass(frozen=True, slots=True)
class BackendResult:
    hits: list[RawHit] = field(default_factory=list)
    total: int | None = None
    has_more: bool | None = None
    insights: tuple[RawInsight, ...] = ()
