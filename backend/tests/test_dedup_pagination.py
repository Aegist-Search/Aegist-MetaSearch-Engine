"""End-to-end dedupe on a crawl with pagination and print-view URLs.

The fixture mirrors a real site: the same article is reachable as ``?page=1``,
``?page=2``, as a print view and with tracking parameters. Only the URL, the
<head> extras and the scripts/styles change, which is exactly what a crawl
collects; the extracted text is identical, so the filter must collapse the
cluster to a single slot and keep the most recently crawled variant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.ingestion.pipeline import ingest_katana_file
from app.utils.urls import stable_id

TITLE = "Guida all'auto usata — Esempio"
H1 = "Guida all'auto usata"
DESCRIPTION = (
    "Come scegliere un'auto usata: controlli, documenti, prezzo e garanzia "
    "in una guida pratica."
)
ARTICLE = (
    "Scegliere un'auto usata richiede pazienza e qualche controllo. "
    "Parti dalla manutenzione documentata, verifica i chilometri reali e "
    "confronta il prezzo con le quotazioni di mercato. "
    "Chiedi sempre il libretto e il certificato di proprietà. "
    "Un test su strada di almeno venti minuti mette in luce rumori e "
    "frizioni che un controllo statico non mostra. "
    "Se possibile fai vedere l'auto a un meccanico di fiducia prima di "
    "firmare: costa poco e può evitare una riparazione costosa. "
    "Ricorda che la garanzia del venditore professionista è almeno un anno. "
) * 3
OTHER_ARTICLE = (
    "L'assicurazione RC auto si rinnova ogni anno e il prezzo dipende dalla "
    "classe di merito, dalla provincia e dal veicolo. "
    "Confronta più preventivi prima della scadenza e controlla le garanzie "
    "accessorie: alcune sono obbligatorie solo se le scegli. "
) * 3

BASE = "https://www.example.com/auto/guida"


def page_html(
    *,
    article: str = ARTICLE,
    title: str = TITLE,
    h1: str = H1,
    head_extra: str = "",
    script: str = "",
) -> str:
    return (
        '<!doctype html><html lang="it"><head><meta charset="utf-8">'
        f"<title>{title}</title>"
        f'<meta name="description" content="{DESCRIPTION}">'
        f"{head_extra}"
        "</head><body>"
        '<header><nav><a href="/">Home</a> <a href="/auto">Auto</a> '
        '<a href="/contatti">Contatti</a></nav></header>'
        f"<article><h1>{h1}</h1><p>{article}</p></article>"
        "<footer>© 2026 Esempio — Tutti i diritti riservati</footer>"
        f"{script}"
        "</body></html>"
    )


def record(url: str, *, body: str, timestamp: str) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "request": {"method": "GET", "endpoint": url},
        "response": {
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "body": body,
        },
    }


CRAWL = [
    record(
        f"{BASE}?page=1",
        body=page_html(script="<script>window.dataLayer = [];</script>"),
        timestamp="2026-01-01T10:00:00Z",
    ),
    record(
        f"{BASE}?page=2",
        body=page_html(head_extra='<meta name="robots" content="noindex">'),
        timestamp="2026-01-02T10:00:00Z",
    ),
    record(
        f"{BASE}/stampa",
        body=page_html(head_extra="<style>@media print { nav { display: none; } }</style>"),
        timestamp="2026-01-03T10:00:00Z",
    ),
    record(
        f"{BASE}/stampa?utm_source=newsletter",
        body=page_html(head_extra="<style>@media print { nav { display: none; } }</style>"),
        timestamp="2026-01-04T10:00:00Z",
    ),
    record(
        "https://www.example.com/auto/assicurazione",
        body=page_html(
            article=OTHER_ARTICLE,
            title="Assicurazione auto: come risparmiare — Esempio",
            h1="Assicurazione auto",
        ),
        timestamp="2026-01-05T10:00:00Z",
    ),
]


class MemoryMeili:
    documents: dict[str, dict[str, Any]] = {}
    uploads: list[list[str]] = []

    def __init__(self, *, base_url: str, master_key: str, index: str) -> None:
        pass

    def __enter__(self) -> "MemoryMeili":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def ensure_index(self) -> bool:
        return True

    def apply_settings(self, settings: dict[str, Any]) -> None:
        pass

    def delete_all_documents(self) -> None:
        type(self).documents = {}

    def add_documents(self, documents: list[dict[str, Any]]) -> int:
        type(self).uploads.append([document["id"] for document in documents])
        for document in documents:
            type(self).documents[document["id"]] = dict(document)
        return len(documents)

    def document_hashes(self, ids: list[str]) -> dict[str, str]:
        return {
            document_id: document["content_hash"]
            for document_id, document in type(self).documents.items()
            if document_id in ids
        }


@pytest.fixture
def crawl_file(tmp_path: Path) -> Path:
    path = tmp_path / "katana.jsonl"
    path.write_text("\n".join(json.dumps(item) for item in CRAWL), encoding="utf-8")
    return path


@pytest.fixture
def memory_meili(monkeypatch: pytest.MonkeyPatch) -> type[MemoryMeili]:
    MemoryMeili.documents = {}
    MemoryMeili.uploads = []
    monkeypatch.setattr("app.ingestion.pipeline.MeiliClient", MemoryMeili)
    return MemoryMeili


def ingest(path: Path, **overrides: object) -> Any:
    options: dict[str, object] = {"index": "pages", "meili_url": "http://meili.test"}
    options.update(overrides)
    return ingest_katana_file(path, **options)  # type: ignore[arg-type]


def test_pagination_and_print_views_share_one_slot(
    crawl_file: Path, memory_meili: type[MemoryMeili]
) -> None:
    report = ingest(crawl_file)

    assert report.records == 5
    assert report.documents == 5
    # ?utm_source=newsletter normalises to the print URL: same primary key.
    assert report.duplicates == 1
    # ?page=2 and the print view carry the same article as ?page=1.
    assert report.near_duplicates == 2
    assert report.indexed == 2

    kept = memory_meili.documents
    print_id = stable_id(f"{BASE}/stampa")
    assert print_id in kept
    assert kept[print_id]["url"] == f"{BASE}/stampa"
    assert stable_id("https://www.example.com/auto/assicurazione") in kept
    assert stable_id(f"{BASE}?page=1") not in kept
    assert stable_id(f"{BASE}?page=2") not in kept


def test_the_most_recent_variant_wins(
    crawl_file: Path, memory_meili: type[MemoryMeili]
) -> None:
    ingest(crawl_file)

    # The print view was crawled last (and refreshed through the tracking URL),
    # so it is the document that survives the cluster.
    print_id = stable_id(f"{BASE}/stampa")
    assert memory_meili.documents[print_id]["crawled_at"] > 0
    assert memory_meili.documents[print_id]["crawled_at"] == 1767520800  # 2026-01-04T10:00Z


def test_second_run_skips_the_remaining_documents(
    crawl_file: Path, memory_meili: type[MemoryMeili]
) -> None:
    ingest(crawl_file)
    second = ingest(crawl_file)

    assert second.indexed == 0
    assert second.unchanged == 2
    assert len(memory_meili.uploads) == 1


def test_without_dedupe_every_unique_url_is_indexed(
    crawl_file: Path, memory_meili: type[MemoryMeili]
) -> None:
    report = ingest(crawl_file, dedupe=False, replace=True)

    assert report.near_duplicates == 0
    assert report.indexed == 4  # 5 records, 1 canonical duplicate
    assert len(memory_meili.documents) == 4


def test_exact_copies_merge_even_with_a_threshold_of_one(
    crawl_file: Path, memory_meili: type[MemoryMeili]
) -> None:
    report = ingest(crawl_file, dedupe_threshold=1.0)

    assert report.near_duplicates == 2
    assert report.indexed == 2
