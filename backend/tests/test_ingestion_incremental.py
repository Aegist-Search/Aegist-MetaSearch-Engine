from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.ingestion.cli import build_parser
from app.ingestion.meili import MeiliIngestError
from app.ingestion.pipeline import ingest_katana_file

BODY = (
    "<html lang=\"it\"><head><title>Auto usata</title>"
    "<meta name=\"description\" content=\"Una guida completa per comprare un'auto usata senza sorprese.\">"
    "</head><body><h1>Auto</h1><p>Testo di prova sulla compravendita.</p></body></html>"
)


class FakeMeili:
    """In-memory stand-in that keeps documents across pipeline runs."""

    documents: dict[str, dict[str, Any]] = {}
    lookups: list[list[str]] = []
    uploads: list[list[str]] = []
    settings_applied: list[dict[str, Any]] = []
    deletions = 0

    def __init__(self, *, base_url: str, master_key: str, index: str) -> None:
        self.index = index

    def __enter__(self) -> "FakeMeili":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def ensure_index(self) -> bool:
        return True

    def apply_settings(self, settings: dict[str, Any]) -> None:
        type(self).settings_applied.append(settings)

    def delete_all_documents(self) -> None:
        type(self).deletions += 1
        type(self).documents = {}

    def add_documents(self, documents: list[dict[str, Any]]) -> int:
        type(self).uploads.append([document["id"] for document in documents])
        for document in documents:
            type(self).documents[document["id"]] = dict(document)
        return len(documents)

    def document_hashes(self, ids: list[str]) -> dict[str, str]:
        type(self).lookups.append(list(ids))
        return {
            document_id: document["content_hash"]
            for document_id, document in type(self).documents.items()
            if document_id in ids
        }


@pytest.fixture
def fake_meili(monkeypatch: pytest.MonkeyPatch) -> type[FakeMeili]:
    FakeMeili.documents = {}
    FakeMeili.lookups = []
    FakeMeili.uploads = []
    FakeMeili.settings_applied = []
    FakeMeili.deletions = 0
    monkeypatch.setattr("app.ingestion.pipeline.MeiliClient", FakeMeili)
    return FakeMeili


def write_crawl(path: Path, pages: list[tuple[str, str, str]]) -> Path:
    """Write one Katana JSONL file with ``(url, body, timestamp)`` pages."""

    lines = [
        json.dumps(
            {
                "timestamp": timestamp,
                "request": {"endpoint": url},
                "response": {
                    "status_code": 200,
                    "content_type": "text/html; charset=utf-8",
                    "body": body,
                },
            }
        )
        for url, body, timestamp in pages
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def ingest(path: Path, **overrides: object) -> Any:
    options: dict[str, object] = {"index": "pages", "meili_url": "http://meili.test"}
    options.update(overrides)
    return ingest_katana_file(path, **options)  # type: ignore[arg-type]


def test_second_crawl_skips_unchanged_documents(
    tmp_path: Path, fake_meili: type[FakeMeili]
) -> None:
    crawl = write_crawl(
        tmp_path / "crawl.jsonl", [("https://example.com/auto", BODY, "2026-01-01T10:00:00Z")]
    )

    first = ingest(crawl)
    second = ingest(crawl)

    assert first.indexed == 1
    assert second.indexed == 0
    assert second.unchanged == 1
    # Only the first run uploaded, and only one document per batch.
    assert [len(batch) for batch in fake_meili.uploads] == [1]
    assert len(fake_meili.lookups) == 2


def test_changed_content_is_uploaded_again(
    tmp_path: Path, fake_meili: type[FakeMeili]
) -> None:
    first_crawl = write_crawl(
        tmp_path / "first.jsonl", [("https://example.com/auto", BODY, "2026-01-01T10:00:00Z")]
    )
    changed = BODY.replace("Testo di prova", "Testo aggiornato")
    second_crawl = write_crawl(
        tmp_path / "second.jsonl",
        [("https://example.com/auto", changed, "2026-02-01T10:00:00Z")],
    )

    ingest(first_crawl)
    report = ingest(second_crawl)

    assert report.unchanged == 0
    assert report.indexed == 1
    assert len(fake_meili.uploads) == 2


def test_a_new_crawl_timestamp_alone_is_not_a_change(
    tmp_path: Path, fake_meili: type[FakeMeili]
) -> None:
    first_crawl = write_crawl(
        tmp_path / "first.jsonl", [("https://example.com/auto", BODY, "2026-01-01T10:00:00Z")]
    )
    later_crawl = write_crawl(
        tmp_path / "later.jsonl", [("https://example.com/auto", BODY, "2026-03-01T10:00:00Z")]
    )

    ingest(first_crawl)
    report = ingest(later_crawl)

    assert report.unchanged == 1
    assert report.indexed == 0


def test_skip_unchanged_can_be_disabled(
    tmp_path: Path, fake_meili: type[FakeMeili]
) -> None:
    crawl = write_crawl(
        tmp_path / "crawl.jsonl", [("https://example.com/auto", BODY, "2026-01-01T10:00:00Z")]
    )

    ingest(crawl)
    report = ingest(crawl, skip_unchanged=False)

    assert report.unchanged == 0
    assert report.indexed == 1
    # Only the first run looked the documents up.
    assert len(fake_meili.lookups) == 1


def test_replace_does_not_compare_with_the_index(
    tmp_path: Path, fake_meili: type[FakeMeili]
) -> None:
    crawl = write_crawl(
        tmp_path / "crawl.jsonl", [("https://example.com/auto", BODY, "2026-01-01T10:00:00Z")]
    )

    report = ingest(crawl, replace=True)

    assert fake_meili.deletions == 1
    assert fake_meili.lookups == []
    assert report.indexed == 1


def test_rejected_hash_lookup_falls_back_to_uploading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Rejecting(FakeMeili):
        def document_hashes(self, ids: list[str]) -> dict[str, str]:
            raise MeiliIngestError("Meilisearch HTTP 400: invalid filter", status=400)

    FakeMeili.documents = {}
    FakeMeili.uploads = []
    monkeypatch.setattr("app.ingestion.pipeline.MeiliClient", Rejecting)
    crawl = write_crawl(
        tmp_path / "crawl.jsonl", [("https://example.com/auto", BODY, "2026-01-01T10:00:00Z")]
    )

    # 400 means "this index cannot be compared" (id not filterable yet): the
    # batch is uploaded normally instead of aborting the run.
    report = ingest(crawl)

    assert report.unchanged == 0
    assert report.indexed == 1


def test_unreachable_index_aborts_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down(FakeMeili):
        def document_hashes(self, ids: list[str]) -> dict[str, str]:
            raise MeiliIngestError("Meilisearch unreachable (ConnectError)")

    FakeMeili.documents = {}
    FakeMeili.uploads = []
    monkeypatch.setattr("app.ingestion.pipeline.MeiliClient", Down)
    crawl = write_crawl(
        tmp_path / "crawl.jsonl", [("https://example.com/auto", BODY, "2026-01-01T10:00:00Z")]
    )

    # A connectivity failure must not degrade into "upload everything" and then
    # fail later with a less precise error.
    with pytest.raises(MeiliIngestError):
        ingest(crawl)


def test_near_duplicate_pages_share_one_slot(
    tmp_path: Path, fake_meili: type[FakeMeili]
) -> None:
    crawl = write_crawl(
        tmp_path / "crawl.jsonl",
        [
            ("https://example.com/auto?page=1", BODY, "2026-01-01T10:00:00Z"),
            ("https://example.com/auto/print", BODY, "2026-01-02T10:00:00Z"),
        ],
    )

    report = ingest(crawl)

    assert report.near_duplicates == 1
    assert report.indexed == 1
    assert len(fake_meili.documents) == 1


def test_near_duplicate_filter_can_be_disabled(
    tmp_path: Path, fake_meili: type[FakeMeili]
) -> None:
    crawl = write_crawl(
        tmp_path / "crawl.jsonl",
        [
            ("https://example.com/auto?page=1", BODY, "2026-01-01T10:00:00Z"),
            ("https://example.com/auto/print", BODY, "2026-01-02T10:00:00Z"),
        ],
    )

    report = ingest(crawl, dedupe=False)

    assert report.near_duplicates == 0
    assert report.indexed == 2


def test_cli_skips_unchanged_by_default() -> None:
    parser = build_parser()

    assert parser.parse_args(["--input", "crawl.jsonl"]).skip_unchanged is True
    assert parser.parse_args(["--input", "crawl.jsonl", "--no-skip-unchanged"]).skip_unchanged is False
    assert parser.parse_args(["--input", "crawl.jsonl"]).dedupe is None
    assert parser.parse_args(["--input", "crawl.jsonl", "--no-dedupe"]).dedupe is False
    assert parser.parse_args(["--input", "crawl.jsonl", "--dedupe-threshold", "0.75"]).dedupe_threshold == 0.75


def test_cli_rejects_an_invalid_threshold() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--input", "crawl.jsonl", "--dedupe-threshold", "0"])
