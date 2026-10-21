from __future__ import annotations

import json

from app.ingestion.index_config import (
    INDEX_SETTINGS,
    ITALIAN_STOP_WORDS,
    expand_synonyms,
)
from app.ingestion.pipeline import ingest_katana_file


def test_settings_include_italian_stop_words_and_synonyms() -> None:
    assert INDEX_SETTINGS["stopWords"] == list(ITALIAN_STOP_WORDS)
    assert INDEX_SETTINGS["stopWords"]

    synonyms = INDEX_SETTINGS["synonyms"]
    assert isinstance(synonyms, dict)
    assert "automobile" in synonyms["auto"]
    assert "auto" in synonyms["automobile"]
    assert "computer" in synonyms["pc"]


def test_crawled_at_is_displayed_and_sortable() -> None:
    assert "crawled_at" in INDEX_SETTINGS["sortableAttributes"]
    assert "crawled_at" in INDEX_SETTINGS["displayedAttributes"]


def test_incremental_ingestion_fields_are_configured() -> None:
    # The lookup by primary key needs a filterable id; the stored hash must be
    # retrievable by the ingestion client.
    assert "id" in INDEX_SETTINGS["filterableAttributes"]
    assert "content_hash" in INDEX_SETTINGS["displayedAttributes"]


def test_elided_italian_forms_are_stop_words() -> None:
    # Meilisearch splits on apostrophes: "l'auto" leaves "l" and "auto".
    for word in ("l", "dell", "nell", "sull", "quest"):
        assert word in ITALIAN_STOP_WORDS
    assert "auto" not in ITALIAN_STOP_WORDS


def test_stop_words_are_lowercase_unique_and_keep_content_words() -> None:
    assert all(word == word.lower() for word in ITALIAN_STOP_WORDS)
    assert len(ITALIAN_STOP_WORDS) == len(set(ITALIAN_STOP_WORDS))
    for word in ("non", "più", "come", "quando", "perché"):
        assert word not in ITALIAN_STOP_WORDS


def test_every_synonym_group_is_bidirectional() -> None:
    synonyms = expand_synonyms()

    for term, alternatives in synonyms.items():
        assert alternatives
        assert term not in alternatives
        for alternative in alternatives:
            assert term in synonyms[alternative]


def test_ingestion_applies_synonyms_and_stop_words(tmp_path, monkeypatch) -> None:
    applied: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_exc: object) -> None:
            pass

        def ensure_index(self) -> bool:
            return True

        def apply_settings(self, settings: dict[str, object]) -> None:
            applied.update(settings)

        def add_documents(self, documents: list[dict[str, object]]) -> int:
            return len(documents)

        def document_hashes(self, ids: list[str]) -> dict[str, str]:
            return {}

    monkeypatch.setattr("app.ingestion.pipeline.MeiliClient", FakeClient)

    body = (
        "<html lang=\"it\"><head><title>Auto usata</title>"
        "<meta name=\"description\" content=\"Una guida completa per comprare un'auto usata senza sorprese.\">"
        "</head><body><h1>Auto</h1><p>Testo di prova.</p></body></html>"
    )
    record = {
        "timestamp": "2026-01-02T03:04:05Z",
        "request": {"endpoint": "https://example.com/auto"},
        "response": {
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "body": body,
        },
    }
    path = tmp_path / "crawl.jsonl"
    path.write_text(json.dumps(record), encoding="utf-8")

    ingest_katana_file(path, index="pages", meili_url="http://meili.test")

    assert applied["stopWords"] == list(ITALIAN_STOP_WORDS)
    synonyms = applied["synonyms"]
    assert isinstance(synonyms, dict)
    assert "automobile" in synonyms["auto"]
