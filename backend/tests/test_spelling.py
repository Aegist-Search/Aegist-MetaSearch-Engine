from __future__ import annotations

from app.services.spelling import closest_word, levenshtein, tolerance_for, words_in_spans
from app.utils.highlight import HighlightSpan


def test_levenshtein_counts_insertions_deletions_and_substitutions() -> None:
    assert levenshtein("intelligenza", "intelligenza") == 0
    assert levenshtein("inteligenza", "intelligenza") == 1
    assert levenshtein("inteliggenza", "intelligenza") == 2
    assert levenshtein("artificale", "artificiale") == 1
    assert levenshtein("kitten", "sitting") == 3


def test_tolerance_matches_the_meilisearch_typo_rules() -> None:
    assert tolerance_for("auto") == 0
    assert tolerance_for("privacy") == 1
    assert tolerance_for("intelligenza") == 2


def test_words_in_spans_reads_only_matched_runs() -> None:
    spans = (
        HighlightSpan("Una guida all'", False),
        HighlightSpan("Intelligenza", True),
        HighlightSpan(" artificiale", False),
        HighlightSpan("artificiale", True),
    )

    assert words_in_spans(spans) == ["Intelligenza", "artificiale"]


def test_closest_word_prefers_the_smallest_distance() -> None:
    candidates = ["artificiale", "artificio", "Artificiale"]

    assert closest_word("artificale", candidates, max_distance=1) == "artificiale"


def test_closest_word_skips_exact_matches_and_distant_words() -> None:
    assert closest_word("privacy", ["privacy"], max_distance=2) is None
    assert closest_word("privacy", ["transparency"], max_distance=2) is None


def test_closest_word_respects_the_distance_limit() -> None:
    # Two edits (one insertion, one substitution): accepted with 2, not with 1.
    assert closest_word("prvaci", ["privacy"], max_distance=2) == "privacy"
    assert closest_word("prvaci", ["privacy"], max_distance=1) is None
