from __future__ import annotations

import pytest

from app.services.results import RawHit
from app.services.search import (
    freshness_score,
    query_terms,
    rerank_local,
    title_match,
)

NOW = 1_800_000_000.0
DAY = 86_400.0


def hit(
    url: str,
    title: str = "page",
    *,
    domain: str = "example.com",
    rank: float | None = 0.9,
    crawled_at: int = int(NOW),
) -> RawHit:
    return RawHit(url=url, title=title, domain=domain, rank=rank, crawled_at=crawled_at)


def urls(hits: list[RawHit]) -> list[str]:
    return [item.url for item in hits]


def test_empty_input_returns_an_empty_list() -> None:
    assert rerank_local([], "privacy", now=NOW) == []


def test_query_terms_are_lowercase_unique_and_ignore_blanks() -> None:
    assert query_terms("  Privacy   PRIVACY auto ") == ("privacy", "auto")
    assert query_terms("   ") == ()


def test_title_match_is_the_share_of_query_terms_found() -> None:
    assert title_match("Privacy guide", ("privacy",)) == 1.0
    assert title_match("Privacy guide", ("privacy", "auto")) == 0.5
    assert title_match("Something else", ("privacy",)) == 0.0
    assert title_match("Privacy guide", ()) == 0.0


def test_freshness_halves_after_the_half_life() -> None:
    assert freshness_score(int(NOW), now=NOW) == pytest.approx(1.0)
    assert freshness_score(int(NOW - 30 * DAY), now=NOW) == pytest.approx(0.5)
    assert freshness_score(int(NOW - 60 * DAY), now=NOW) == pytest.approx(0.25)


def test_freshness_is_zero_without_a_timestamp_or_in_the_future_is_capped() -> None:
    assert freshness_score(0, now=NOW) == 0.0
    assert freshness_score(int(NOW + 10 * DAY), now=NOW) == pytest.approx(1.0)


def test_newer_page_wins_at_equal_relevance() -> None:
    hits = [
        hit("https://old.example/a", domain="old.example", crawled_at=int(NOW - 365 * DAY)),
        hit("https://new.example/b", domain="new.example", crawled_at=int(NOW - DAY)),
    ]

    assert urls(rerank_local(hits, "privacy", now=NOW)) == [
        "https://new.example/b",
        "https://old.example/a",
    ]


def test_title_match_can_lift_a_result() -> None:
    hits = [
        hit("https://a.example/1", "Something else", domain="a.example", rank=0.90),
        hit("https://b.example/2", "Privacy guide", domain="b.example", rank=0.88),
    ]

    assert urls(rerank_local(hits, "privacy", now=NOW)) == [
        "https://b.example/2",
        "https://a.example/1",
    ]


def test_repeated_domain_is_pushed_down() -> None:
    hits = [
        hit("https://a.example/1", domain="a.example"),
        hit("https://a.example/2", domain="a.example"),
        hit("https://b.example/3", domain="b.example"),
    ]

    # The second page from a.example pays the 0.06 penalty and drops below the
    # equally ranked page from another domain.
    assert urls(rerank_local(hits, "privacy", now=NOW)) == [
        "https://a.example/1",
        "https://b.example/3",
        "https://a.example/2",
    ]


def test_domain_penalty_is_capped() -> None:
    hits = [hit(f"https://a.example/{index}", domain="a.example") for index in range(8)]

    ordered = rerank_local(hits, "privacy", now=NOW)

    # Every page has the same relevance and freshness, so only the increasing
    # penalty changes the order: it never excludes a page, it just sorts it.
    assert urls(ordered) == [f"https://a.example/{index}" for index in range(8)]


def test_missing_signals_do_not_break_the_ranking() -> None:
    hits = [
        hit("https://a.example/1", domain="a.example", rank=None, crawled_at=0),
        hit("https://b.example/2", domain="b.example", rank=None, crawled_at=0),
    ]

    assert urls(rerank_local(hits, "privacy", now=NOW)) == [
        "https://a.example/1",
        "https://b.example/2",
    ]


def test_ties_keep_the_incoming_order_and_input_is_not_mutated() -> None:
    hits = [
        hit("https://a.example/1", domain="a.example", rank=None, crawled_at=0),
        hit("https://b.example/2", domain="b.example", rank=None, crawled_at=0),
    ]

    ordered = rerank_local(hits, "privacy", now=NOW)

    assert ordered is not hits
    assert urls(ordered) == urls(hits)
    assert urls(hits) == ["https://a.example/1", "https://b.example/2"]


def test_out_of_range_rank_is_clamped() -> None:
    hits = [
        hit("https://a.example/1", domain="a.example", rank=4.2),
        hit("https://b.example/2", domain="b.example", rank=1.0),
    ]

    assert urls(rerank_local(hits, "privacy", now=NOW)) == [
        "https://a.example/1",
        "https://b.example/2",
    ]
