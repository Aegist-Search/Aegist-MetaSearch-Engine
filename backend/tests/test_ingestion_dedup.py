from __future__ import annotations

import pytest

from app.ingestion.dedup import (
    dedupe_documents,
    document_hash,
    hamming,
    simhash,
    similarity,
)

AUTO = (
    "Guida completa all'acquisto di un'auto usata: controlli, documenti, "
    "prezzo e garanzia. "
) * 10
SPORT = (
    "Risultati della partita di ieri sera: doppietta dell'attaccante e "
    "classifica aggiornata dopo il recupero. "
) * 10


def document(url: str, content: str, crawled_at: int = 0) -> dict[str, object]:
    return {"id": url, "url": url, "content": content, "crawled_at": crawled_at}


def test_exact_duplicate_keeps_the_newest_crawl() -> None:
    result = dedupe_documents(
        [document("old", AUTO, crawled_at=100), document("new", AUTO, crawled_at=200)]
    )

    assert [item["id"] for item in result.documents] == ["new"]
    assert result.dropped == 1


def test_older_variant_does_not_replace_a_newer_one() -> None:
    result = dedupe_documents(
        [document("new", AUTO, crawled_at=300), document("old", AUTO, crawled_at=100)]
    )

    assert [item["id"] for item in result.documents] == ["new"]
    assert result.dropped == 1


def test_lightly_edited_page_is_a_near_duplicate() -> None:
    edited = AUTO + " Aggiunta finale sui costi di passaggio di proprietà."

    assert similarity(simhash(AUTO), simhash(edited)) >= 0.9

    result = dedupe_documents(
        [document("a", AUTO, crawled_at=100), document("b", edited, crawled_at=200)],
        threshold=0.9,
    )

    assert [item["id"] for item in result.documents] == ["b"]
    assert result.dropped == 1


def test_unrelated_content_is_kept() -> None:
    result = dedupe_documents([document("auto", AUTO), document("sport", SPORT)])

    assert [item["id"] for item in result.documents] == ["auto", "sport"]
    assert result.dropped == 0


def test_only_the_content_prefix_is_compared() -> None:
    head = "Guida completa all'auto usata. " * 20
    first = head + "prima parte " * 40
    second = head + "seconda parte " * 40

    shared = dedupe_documents(
        [document("a", first), document("b", second)],
        prefix_chars=len(head),
    )
    different = dedupe_documents([document("a", first), document("b", second)])

    assert shared.dropped == 1
    assert different.dropped == 0


def test_documents_without_content_are_never_dropped() -> None:
    result = dedupe_documents(
        [document("a", ""), document("b", "   "), {"id": "c", "url": "c"}]
    )

    assert [item["id"] for item in result.documents] == ["a", "b", "c"]
    assert result.dropped == 0
    assert document_hash({"id": "a", "content": ""}) == 0


def test_duplicates_keep_the_slot_of_the_first_occurrence() -> None:
    result = dedupe_documents(
        [
            document("a", AUTO, crawled_at=10),
            document("b", SPORT),
            document("a-copy", AUTO, crawled_at=20),
        ]
    )

    # The survivor of the first cluster takes the first slot, so the batch
    # order is deterministic even when the newer document is the last one.
    assert [item["id"] for item in result.documents] == ["a-copy", "b"]
    assert result.dropped == 1


def test_threshold_out_of_range_is_rejected() -> None:
    for value in (0, -0.5, 1.5):
        with pytest.raises(ValueError):
            dedupe_documents([document("a", AUTO)], threshold=value)


def test_similarity_and_hamming_edges() -> None:
    assert similarity(0, 0) == 1.0
    assert similarity(0, (1 << 64) - 1) == 0.0
    assert hamming(0b1011, 0b0010) == 2
    assert simhash("") == 0
    assert simhash(AUTO) == simhash(AUTO)
