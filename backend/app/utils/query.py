"""Search-operator parsing, shared by the search and suggest endpoints.

The user can type a small, predictable subset of operators:

* ``site:example.com`` — restrict the local index to one domain;
* ``"frase esatta"`` — a quoted phrase, kept verbatim so Meilisearch does the
  adjacency matching instead of us;
* ``-parola`` — drop results whose title, snippet or h1 contains the term.

Parsing is a pure function with no I/O, so it is cheap to test and it runs
before any backend is called. Operators are *local*: the cleaned text (never
the raw operators) is what reaches the external providers, so a ``site:``
restriction cannot silently turn into a provider-side query.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Sequence

# A hostname: at least one dot, letters/digits/hyphens per label, no spaces.
_HOST_RE = re.compile(
    r"^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_MAX_TERMS = 16


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    """Result of parsing one raw query string."""

    text: str
    """Query without the operators: this is what the search backends receive."""

    sites: tuple[str, ...] = ()
    """Domains from ``site:``, normalised to the stored ``domain`` value."""

    excluded: tuple[str, ...] = ()
    """Terms from ``-term``; they never reach the index as negative filters."""

    def __bool__(self) -> bool:
        return bool(self.text)


def normalize_site(raw: str) -> str:
    """Turn ``https://www.Example.com/path`` into ``example.com``.

    Returns an empty string when the value cannot be a hostname. The result
    matches ``domain_of`` in ``app.utils.urls``: lower case and without a
    leading ``www.``.
    """

    value = raw.strip().strip('"').strip("'").casefold()
    if not value:
        return ""
    if "://" in value:
        value = value.split("://", 1)[1]
    value = re.split(r"[/?#]", value, maxsplit=1)[0]
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    value = value.split(":", 1)[0].strip(".")
    if value.startswith("www."):
        value = value[4:]
    if not _HOST_RE.match(value):
        return ""
    return value


def _tokens(raw: str) -> list[str]:
    """Split on whitespace, keeping quoted spans (and their quotes) together."""

    tokens: list[str] = []
    buffer: list[str] = []
    quoted = False
    for char in raw:
        if char == '"':
            quoted = not quoted
            buffer.append(char)
            continue
        if char.isspace() and not quoted:
            if buffer:
                tokens.append("".join(buffer))
                buffer = []
            continue
        buffer.append(char)
    if buffer:
        tokens.append("".join(buffer))
    return tokens


def _phrase(token: str) -> str | None:
    """Content of a quoted token, or ``None`` when it is not quoted.

    The quotes must be checked before slicing: ``-usata`` would otherwise
    lose its first and last character and become ``sat``.
    """

    if len(token) <= 2 or not (token.startswith('"') and token.endswith('"')):
        return None
    inner = token[1:-1].strip()
    return inner or None


def parse_query(raw: str) -> ParsedQuery:
    """Parse operators out of ``raw`` without losing the plain words.

    Malformed operators are treated as ordinary text instead of being
    silently dropped: ``site:`` alone, ``site:localhost`` (not a hostname) or
    an unbalanced quote all stay searchable, so nothing disappears from the
    query without the user seeing it.
    """

    text: list[str] = []
    sites: list[str] = []
    excluded: list[str] = []

    for token in _tokens(raw):
        if len(token) > 5 and token[:5].casefold() == "site:":
            site = normalize_site(token[5:])
            if site:
                if site not in sites and len(sites) < _MAX_TERMS:
                    sites.append(site)
            else:
                text.append(token)
            continue

        if len(token) > 1 and token.startswith("-"):
            inner = _phrase(token[1:]) or token[1:].strip()
            if inner and len(excluded) < _MAX_TERMS:
                term = inner.casefold()
                if term not in excluded:
                    excluded.append(term)
            continue

        # Plain words and quoted phrases both stay in the text: the quotes are
        # kept verbatim so Meilisearch itself matches the phrase adjacency.
        text.append(token)

    if not text:
        # ``site:example.com`` on its own is still a meaningful search: use the
        # first domain as text so the providers receive something searchable.
        text = list(sites[:1])

    return ParsedQuery(
        text=" ".join(text),
        sites=tuple(sites),
        excluded=tuple(excluded),
    )


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_filter(
    *,
    sites: Sequence[str] = (),
    domains: Sequence[str] = (),
    langs: Sequence[str] = (),
) -> str | None:
    """Build a Meilisearch filter, or ``None`` when nothing is restricted.

    Values are validated by the caller (``parse_query`` for ``site:``, the
    regexes in the route for the query parameters) and quoted here, so the
    string can only ever contain validated hostnames and language codes.
    """

    clauses: list[str] = []
    for group in (sites, domains):
        values = [value for value in dict.fromkeys(group) if _HOST_RE.match(value)]
        if values:
            clauses.append("(" + " OR ".join(f"domain = {_quote(v)}" for v in values) + ")")
    languages = [value for value in dict.fromkeys(langs) if value]
    if languages:
        clauses.append("(" + " OR ".join(f"lang = {_quote(v)}" for v in languages) + ")")
    return " AND ".join(clauses) if clauses else None


def is_excluded(hit_text: Sequence[str], excluded: Sequence[str]) -> bool:
    """True when any of the ``-term`` values appears in the given fields."""

    if not excluded:
        return False
    haystack = " \u0001 ".join(field.casefold() for field in hit_text if field)
    return any(term in haystack for term in excluded)
