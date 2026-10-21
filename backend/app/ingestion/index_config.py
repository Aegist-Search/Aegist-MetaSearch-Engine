"""Meilisearch index settings applied by the ingestion CLI.

The Italian stop words remove only function words (articles, prepositions,
auxiliaries, pronouns). Content-bearing words such as "non", "più", "come" and
"quando" stay searchable, because dropping them changes the meaning of a query.
Synonym groups are expanded into a bidirectional map: Meilisearch resolves
synonyms per key, so every term needs its own entry.
"""

from __future__ import annotations

from typing import Any

_ARTICLES = ("il", "lo", "la", "i", "gli", "le", "un", "uno", "una")
_PREPOSITIONS = (
    "di",
    "del",
    "dello",
    "della",
    "dei",
    "degli",
    "delle",
    "a",
    "ad",
    "al",
    "allo",
    "alla",
    "ai",
    "agli",
    "alle",
    "da",
    "dal",
    "dallo",
    "dalla",
    "dai",
    "dagli",
    "dalle",
    "in",
    "nel",
    "nello",
    "nella",
    "nei",
    "negli",
    "nelle",
    "con",
    "col",
    "coi",
    "su",
    "sul",
    "sullo",
    "sulla",
    "sui",
    "sugli",
    "sulle",
    "per",
    "tra",
    "fra",
)
_CONJUNCTIONS = ("e", "ed", "o", "od", "ma", "né", "oppure", "però")
_AUXILIARIES = (
    "è",
    "sono",
    "era",
    "erano",
    "essere",
    "stato",
    "stata",
    "stati",
    "state",
    "ho",
    "hai",
    "ha",
    "abbiamo",
    "avete",
    "hanno",
    "aveva",
    "avevano",
)
_PRONOUNS = (
    "io",
    "tu",
    "lui",
    "lei",
    "noi",
    "voi",
    "loro",
    "mi",
    "ti",
    "ci",
    "vi",
    "si",
    "ne",
    "che",
    "cui",
)
_DEMONSTRATIVES = (
    "questo",
    "questa",
    "questi",
    "queste",
    "quello",
    "quella",
    "quelli",
    "quelle",
)
_ADVERBS = ("anche", "già")
# Italian elision: Meilisearch splits "l'auto" and "nell'aria" on the
# apostrophe, so the truncation left behind ("l", "nell") becomes a token of
# its own. It is a function word, not content: drop it.
_ELISIONS = ("l", "d", "dell", "dall", "nell", "sull", "coll", "all", "quest", "anch")

# dict.fromkeys preserves order while dropping duplicates (articles such as
# "la" and "le" also appear among the pronouns).
ITALIAN_STOP_WORDS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *_ARTICLES,
            *_PREPOSITIONS,
            *_CONJUNCTIONS,
            *_AUXILIARIES,
            *_PRONOUNS,
            *_DEMONSTRATIVES,
            *_ADVERBS,
            *_ELISIONS,
        )
    )
)

# Plain-language equivalences, not translations: each term is mapped to the
# other members of its group so a query in either direction finds both pages.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("auto", "automobile", "macchina", "vettura"),
    ("pc", "computer"),
    ("smartphone", "cellulare"),
    ("tv", "televisione"),
    ("bici", "bicicletta"),
    ("moto", "motocicletta"),
    ("frigo", "frigorifero"),
    ("foto", "fotografia"),
)


def expand_synonyms(
    groups: tuple[tuple[str, ...], ...] = _SYNONYM_GROUPS,
) -> dict[str, list[str]]:
    """Turn synonym groups into the per-term map Meilisearch expects."""

    synonyms: dict[str, list[str]] = {}
    for group in groups:
        for term in group:
            synonyms[term] = [other for other in group if other != term]
    return synonyms


INDEX_SETTINGS: dict[str, Any] = {
    "searchableAttributes": ["title", "h1", "snippet", "content", "domain"],
    # ``id`` is filterable for the incremental ingestion only: it looks up the
    # stored documents of a batch by primary key. The public API never exposes
    # a user-supplied Meilisearch filter.
    "filterableAttributes": ["domain", "lang", "source", "id"],
    "sortableAttributes": ["crawled_at"],
    "displayedAttributes": [
        "id",
        "title",
        "url",
        "snippet",
        "h1",
        "domain",
        "lang",
        "image",
        "crawled_at",
        "content_hash",
    ],
    "rankingRules": ["words", "typo", "proximity", "attribute", "sort", "exactness"],
    "typoTolerance": {
        "enabled": True,
        "minWordSizeForTypos": {"oneTypo": 5, "twoTypos": 9},
    },
    "pagination": {"maxTotalHits": 1000},
    "faceting": {"maxValuesPerFacet": 100},
    "stopWords": list(ITALIAN_STOP_WORDS),
    "synonyms": expand_synonyms(),
}
