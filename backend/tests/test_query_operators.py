from __future__ import annotations

import pytest

from app.utils.query import (
    build_filter,
    is_excluded,
    normalize_site,
    parse_query,
)


def test_plain_query_is_untouched() -> None:
    parsed = parse_query("auto  usata")

    assert parsed.text == "auto usata"
    assert parsed.sites == ()
    assert parsed.excluded == ()


def test_site_operator_accepts_urls_and_www() -> None:
    parsed = parse_query("site:https://www.Example.com/path?q=1 auto")

    assert parsed.sites == ("example.com",)
    assert "site:" not in parsed.text
    assert parsed.text == "auto"


def test_multiple_sites_are_deduplicated() -> None:
    parsed = parse_query("site:example.com site:www.example.com site:other.org")

    assert parsed.sites == ("example.com", "other.org")
    # No free text: the first site becomes the searchable text.
    assert parsed.text == "example.com"


def test_quoted_phrase_is_kept_with_its_quotes() -> None:
    parsed = parse_query('"frase esatta" guida')

    assert parsed.text == '"frase esatta" guida'


def test_exclusion_operator() -> None:
    parsed = parse_query("auto -usata -spam")

    assert parsed.text == "auto"
    assert parsed.excluded == ("usata", "spam")


def test_exclusion_of_a_quoted_phrase() -> None:
    parsed = parse_query('guida -"auto usata"')

    assert parsed.text == "guida"
    assert parsed.excluded == ("auto usata",)


def test_empty_site_operator_stays_searchable_text() -> None:
    parsed = parse_query("site:")

    assert parsed.sites == ()
    assert parsed.text == "site:"


def test_invalid_hostname_is_not_silently_dropped() -> None:
    parsed = parse_query("site:localhost guida")

    assert parsed.sites == ()
    assert parsed.text == "site:localhost guida"


def test_unbalanced_quote_is_treated_as_text() -> None:
    parsed = parse_query('"frase non chiusa')

    assert parsed.sites == ()
    assert '"frase non chiusa' in parsed.text
    assert parse_query("auto").text == "auto"


def test_only_exclusions_leave_no_searchable_text() -> None:
    parsed = parse_query("-spam")

    assert parsed.text == ""
    assert not parsed


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.com/path", "example.com"),
        ("www.example.com", "example.com"),
        ("example.com:8080", "example.com"),
        ("example.com.", "example.com"),
        ("user@example.com", "example.com"),
        ("example", ""),
        ("", ""),
    ],
)
def test_normalize_site(raw: str, expected: str) -> None:
    assert normalize_site(raw) == expected


def test_build_filter_combines_groups_with_and() -> None:
    filters = build_filter(
        sites=("example.com",),
        domains=("other.org",),
        langs=("it",),
    )

    assert filters == '(domain = "example.com") AND (domain = "other.org") AND (lang = "it")'


def test_build_filter_returns_none_without_restrictions() -> None:
    assert build_filter() is None
    assert build_filter(sites=("not a host",)) is None
    assert build_filter(langs=("",)) is None


def test_build_filter_quotes_values() -> None:
    # Values are validated hostnames, but quoting keeps the filter syntax
    # unambiguous even if the caller passes something odd.
    assert build_filter(sites=("example.com",)) == '(domain = "example.com")'


def test_is_excluded_matches_any_field_case_insensitively() -> None:
    assert is_excluded(("Guida Auto", "", "", "example.com"), ("auto",))
    assert is_excluded(("Titolo", "snippet", "", "example.com"), ("example.com",))
    assert not is_excluded(("Titolo", "snippet", "", "example.com"), ("moto",))
    assert not is_excluded(("Titolo",), ())
