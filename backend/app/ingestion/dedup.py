"""Near-duplicate detection for the ingestion pipeline.

Pagination, print views and mirrors publish almost the same page under
different URLs, and each copy would take a slot in the results page. A
SimHash fingerprint over the first ``prefix_chars`` characters of the
extracted ``content`` catches those variants: identical text maps to the same
64-bit value, a slightly edited page to a value that differs in only a few
bits, so a Hamming-distance threshold decides whether two documents are the
same page.

``dedupe_documents`` is pure and works on a list, which keeps it trivially
testable; the pipeline calls it once per batch, before the Meilisearch upload.
Within a cluster the most recently crawled document wins: a refreshed page
replaces the stale copy instead of being dropped by it.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_THRESHOLD = 0.9
DEFAULT_PREFIX_CHARS = 2_000
_BITS = 64
_BANDS = 4
_TOKEN_RE = re.compile(r"[0-9a-zà-öø-ÿ]+")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; punctuation and markup never reach the hash."""

    return _TOKEN_RE.findall(text.casefold())


def simhash(text: str, *, bits: int = _BITS) -> int:
    """Hash of ``text`` whose bits flip when the token set changes.

    Returns 0 for an empty text, which the callers read as "no fingerprint"
    and therefore "never a duplicate": a page without content must not be
    dropped just because it is as empty as another page.
    """

    tokens = tokenize(text)
    if not tokens:
        return 0

    totals = [0] * bits
    for token, weight in Counter(tokens).items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(bits):
            totals[bit] += weight if (value >> bit) & 1 else -weight

    result = 0
    for bit, total in enumerate(totals):
        if total > 0:
            result |= 1 << bit
    return result


def hamming(a: int, b: int) -> int:
    """Number of differing bits between two fingerprints."""

    return bin(a ^ b).count("1")


def similarity(a: int, b: int, *, bits: int = _BITS) -> float:
    """1.0 for identical fingerprints, 0.0 for completely different ones."""

    if a == b:
        return 1.0
    return 1.0 - hamming(a, b) / bits


def document_hash(document: Mapping[str, Any], *, prefix_chars: int = DEFAULT_PREFIX_CHARS) -> int:
    """Fingerprint of the content prefix used for near-duplicate detection."""

    content = document.get("content")
    if not isinstance(content, str) or not content.strip():
        return 0
    return simhash(content[: max(1, prefix_chars)])


def _bands(fingerprint: int) -> list[tuple[int, int]]:
    width = _BITS // _BANDS
    mask = (1 << width) - 1
    return [(band, (fingerprint >> (band * width)) & mask) for band in range(_BANDS)]


def _crawled_at(document: Mapping[str, Any]) -> int:
    value = document.get("crawled_at")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@dataclass(frozen=True, slots=True)
class DedupResult:
    documents: list[dict[str, Any]]
    dropped: int


def dedupe_documents(
    documents: Sequence[dict[str, Any]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    prefix_chars: int = DEFAULT_PREFIX_CHARS,
) -> DedupResult:
    """Drop near-duplicates, keeping the most recently crawled variant.

    Args:
        documents: Extracted documents, in crawl order.
        threshold: Minimum similarity (0..1] to consider two pages identical.
            ``1.0`` only drops exact fingerprints.
        prefix_chars: Characters of ``content`` fed to SimHash.

    Returns:
        The kept documents in their original slot order and the drop count.
        A document without content is always kept; a replacement keeps the
        original slot so the output order stays deterministic.

    Raises:
        ValueError: ``threshold`` is outside ``(0, 1]``.
    """

    if not 0 < threshold <= 1:
        raise ValueError("threshold must be greater than 0 and at most 1")

    slots: list[tuple[int, dict[str, Any]]] = []
    buckets: dict[tuple[int, int], list[int]] = {}

    for document in documents:
        fingerprint = document_hash(document, prefix_chars=prefix_chars)

        if fingerprint == 0:
            slots.append((0, document))
            continue

        candidates = sorted(
            {slot for band in _bands(fingerprint) for slot in buckets.get(band, ())}
        )
        match: int | None = None
        for slot in candidates:
            existing_fingerprint, _ = slots[slot]
            if existing_fingerprint and similarity(fingerprint, existing_fingerprint) >= threshold:
                match = slot
                break

        if match is None:
            slot = len(slots)
            slots.append((fingerprint, document))
            for band in _bands(fingerprint):
                buckets.setdefault(band, []).append(slot)
            continue

        if _crawled_at(document) > _crawled_at(slots[match][1]):
            # Same slot, newer document: the refreshed page wins without
            # reordering the batch. Stale bands may still point here, but the
            # similarity check compares the fingerprint stored in the slot.
            slots[match] = (fingerprint, document)
            for band in _bands(fingerprint):
                bucket = buckets.setdefault(band, [])
                if match not in bucket:
                    bucket.append(match)

    return DedupResult(
        documents=[document for _, document in slots],
        dropped=len(documents) - len(slots),
    )
