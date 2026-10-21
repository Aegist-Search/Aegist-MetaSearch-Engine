"""Parse Meilisearch ``_formatted`` values into safe text segments.

Meilisearch marks matched words by wrapping them in ``<em>``/``</em>``. The
frontend must never inject that markup as HTML: the tags are added around text
that comes from indexed pages, so the parser returns plain segments and React
renders the highlight itself. Unmatched or nested tags stay in the text as
literal characters instead of being dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAG_RE = re.compile(r"<em>|</em>")
_OPEN = "<em>"


@dataclass(frozen=True, slots=True)
class HighlightSpan:
    """One run of text, either matched by the query or not."""

    text: str
    match: bool = False


def parse_highlighted(value: str | None) -> tuple[HighlightSpan, ...]:
    """Split a highlighted value into plain and matched spans.

    Only balanced ``<em>`` pairs are treated as markup. Returns an empty tuple
    when there is nothing to highlight, so callers can fall back to the plain
    field.
    """

    if not isinstance(value, str) or not value:
        return ()

    tags = list(_TAG_RE.finditer(value))
    if not tags:
        return ()

    # Track nesting with a depth counter: only the outermost pair is markup,
    # so the intervals can never overlap when a crawled page happens to
    # contain a literal <em> inside a matched phrase.
    depth = 0
    opening: re.Match[str] | None = None
    intervals: list[tuple[int, int, int, int]] = []

    for tag in tags:
        if tag.group() == _OPEN:
            if depth == 0:
                opening = tag
            depth += 1
        elif depth > 0:
            depth -= 1
            if depth == 0 and opening is not None:
                intervals.append((opening.start(), opening.end(), tag.start(), tag.end()))
                opening = None

    if not intervals:
        return ()

    spans: list[HighlightSpan] = []

    def add(text: str, match: bool) -> None:
        if not text:
            return
        if spans and spans[-1].match == match:
            previous = spans.pop()
            spans.append(HighlightSpan(previous.text + text, match))
        else:
            spans.append(HighlightSpan(text, match))

    cursor = 0
    for open_start, open_end, content_end, close_end in intervals:
        add(value[cursor:open_start], False)
        add(value[open_end:content_end], True)
        cursor = close_end

    add(value[cursor:], False)
    return tuple(spans)
