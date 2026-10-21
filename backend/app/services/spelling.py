"""Term-level spelling helpers for the "did you mean" suggestion.

Meilisearch has no spellchecker. It *tolerates* typos while searching, and the
highlighted runs of a matching document contain the spelling stored in the
index: those runs are the candidates compared here with the query term.

Everything in this module is pure (no I/O), so the comparison is easy to test
and independent from the client.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.utils.highlight import HighlightSpan

# Words of at least three letters, Unicode-aware: no digits, no punctuation.
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def levenshtein(left: str, right: str) -> int:
    """Classic edit distance (insertions, deletions, substitutions)."""

    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, 1):
        current = [row]
        for column, right_char in enumerate(right, 1):
            cost = 0 if left_char == right_char else 1
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + cost,
                )
            )
        previous = current
    return previous[-1]


def tolerance_for(term: str) -> int:
    """How many edits Meilisearch itself tolerates for a word of this length.

    Meilisearch allows one typo for words of 5-8 characters and two from 9 on.
    Shorter words must be exact, so a "correction" would be invented.
    """

    if len(term) >= 9:
        return 2
    if len(term) >= 5:
        return 1
    return 0


def words_in_spans(spans: Iterable[HighlightSpan]) -> list[str]:
    """Words from the *matched* highlight runs, in document order."""

    words: list[str] = []
    for span in spans:
        if not span.match:
            continue
        words.extend(_WORD_RE.findall(span.text))
    return words


def closest_word(
    original: str,
    candidates: Iterable[str],
    *,
    max_distance: int,
) -> str | None:
    """Closest candidate spelling within ``max_distance``, or ``None``.

    An exact match is not a correction, so it is skipped. Ties keep the first
    candidate, which is the order Meilisearch returned (relevance).
    """

    best: tuple[int, str] | None = None
    seen: set[str] = set()

    for candidate in candidates:
        key = candidate.casefold()
        if key == original.casefold() or key in seen:
            continue
        seen.add(key)

        distance = levenshtein(original.casefold(), key)
        if distance == 0 or distance > max_distance:
            continue
        if best is None or distance < best[0]:
            best = (distance, candidate)

    return best[1] if best is not None else None
