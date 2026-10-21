from __future__ import annotations

import json

import pytest

from app.ingestion.katana import extract_document, iter_katana_records
from app.utils.urls import domain_of, normalize_url, stable_id, url_key

HTML = """<!doctype html>
<html lang="it-IT">
  <head>
    <title>Guida alla privacy — Aegis</title>
    <meta name="description" content="Una guida pratica per proteggere la propria navigazione e ridurre le tracce lasciate online.">
  </head>
  <body>
    <h1>Privacy sul web</h1>
    <script>window.tracker = "do-not-index-me";</script>
    <p>Questo testo parla di come funziona la ricerca privata.</p>
  </body>
</html>"""


def make_record(
    url: str = "https://www.example.com/privacy/?utm_source=newsletter#top",
    *,
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
    body: str = HTML,
    timestamp: str = "2026-01-02T03:04:05Z",
) -> dict:
    return {
        "timestamp": timestamp,
        "request": {"method": "GET", "endpoint": url},
        "response": {
            "status_code": status,
            "content_type": content_type,
            "body": body,
        },
    }


class TestUrls:
    def test_normalize_strips_fragment_tracking_and_default_port(self) -> None:
        assert (
            normalize_url("HTTPS://WWW.Example.com:443/Path/?utm_source=a&id=1#section")
            == "https://www.example.com/Path?id=1"
        )

    def test_normalize_removes_trailing_slash_but_keeps_root(self) -> None:
        assert normalize_url("http://example.com") == "http://example.com/"
        assert normalize_url("http://example.com/a/b/") == "http://example.com/a/b"

    @pytest.mark.parametrize(
        "value",
        ["", None, "javascript:alert(1)", "mailto:hi@example.com", "ftp://example.com", "http://:80/x", "not a url"],
    )
    def test_normalize_rejects_unusable_urls(self, value: str | None) -> None:
        assert normalize_url(value) is None

    def test_url_key_is_scheme_and_www_insensitive(self) -> None:
        assert url_key("https://www.Example.com/a/?utm_campaign=x") == url_key("http://example.com/a")

    def test_url_key_keeps_meaningful_query(self) -> None:
        assert url_key("https://example.com/search?q=privacy") != url_key("https://example.com/search?q=autre")

    def test_domain_of_strips_www(self) -> None:
        assert domain_of("https://www.example.com/a") == "example.com"

    def test_stable_id_is_deterministic(self) -> None:
        first = stable_id("https://example.com/a")
        second = stable_id("https://example.com/a#fragment")
        assert first == second
        assert len(first) == 40


class TestExtraction:
    def test_extracts_clean_document(self) -> None:
        document = extract_document(make_record())

        assert document is not None
        assert document["url"] == "https://www.example.com/privacy"
        assert document["title"] == "Guida alla privacy — Aegis"
        assert document["h1"] == "Privacy sul web"
        assert document["domain"] == "example.com"
        assert document["lang"] == "it"
        assert document["status_code"] == 200
        assert document["source"] == "katana"
        assert "ricerca privata" in document["content"]
        assert "do-not-index-me" not in document["content"]
        assert document["snippet"].startswith("Una guida pratica")
        assert document["crawled_at"] > 0
        assert document["id"] == stable_id(document["url"])

    def test_og_title_takes_precedence(self) -> None:
        body = HTML.replace(
            "<title>Guida alla privacy — Aegis</title>",
            '<title>Guida alla privacy — Aegis</title><meta property="og:title" content="Titolo social">',
        )
        document = extract_document(make_record(body=body))
        assert document is not None
        assert document["title"] == "Titolo social"

    def test_og_image_is_resolved_against_the_page_url(self) -> None:
        body = HTML.replace(
            "</head>",
            '<meta property="og:image" content="/assets/cover.png"></head>',
        )
        document = extract_document(make_record(body=body))

        assert document is not None
        assert document["image"] == "https://www.example.com/assets/cover.png"

    def test_absolute_og_image_is_kept(self) -> None:
        body = HTML.replace(
            "</head>",
            '<meta property="og:image" content="https://cdn.example.net/cover.jpg"></head>',
        )
        document = extract_document(make_record(body=body))

        assert document is not None
        assert document["image"] == "https://cdn.example.net/cover.jpg"

    def test_missing_og_image_leaves_the_field_empty(self) -> None:
        document = extract_document(make_record())

        assert document is not None
        assert document["image"] == ""

    @pytest.mark.parametrize(
        "value",
        ["javascript:alert(1)", "data:image/png;base64,AAAA", "mailto:cover@example.com"],
    )
    def test_non_http_og_image_is_ignored(self, value: str) -> None:
        body = HTML.replace("</head>", f'<meta property="og:image" content="{value}"></head>')
        document = extract_document(make_record(body=body))

        assert document is not None
        assert document["image"] == ""

    def test_skips_error_status(self) -> None:
        assert extract_document(make_record(status=404)) is None

    def test_skips_non_html_resources(self) -> None:
        assert extract_document(make_record(content_type="application/javascript", body="var x = 1;")) is None
        assert extract_document(make_record(content_type="application/pdf", body="%PDF-1.7")) is None

    def test_missing_body_is_skipped_by_default(self) -> None:
        assert extract_document(make_record(body="")) is None

    def test_missing_body_can_be_indexed_with_url_title(self) -> None:
        document = extract_document(make_record(body=""), require_body=False)
        assert document is not None
        assert document["title"] == "Privacy"
        assert document["h1"] == ""
        assert document["snippet"] == ""

    def test_non_http_endpoint_is_rejected(self) -> None:
        record = make_record(url="mailto:info@example.com")
        assert extract_document(record) is None

    def test_content_hash_ignores_the_crawl_timestamp(self) -> None:
        first = extract_document(make_record(timestamp="2026-01-01T00:00:00Z"))
        second = extract_document(make_record(timestamp="2026-06-01T00:00:00Z"))

        assert first is not None and second is not None
        assert first["crawled_at"] != second["crawled_at"]
        assert first["content_hash"] == second["content_hash"]

    def test_content_hash_changes_with_the_extracted_text(self) -> None:
        base = extract_document(make_record())
        changed = extract_document(
            make_record(body=HTML.replace("ricerca privata", "navigazione privata"))
        )

        assert base is not None and changed is not None
        assert base["content_hash"] != changed["content_hash"]


class TestReader:
    def test_reads_jsonl(self, tmp_path) -> None:
        path = tmp_path / "crawl.jsonl"
        records = [make_record("https://example.com/a"), make_record("https://example.com/b")]
        path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")

        parsed = list(iter_katana_records(path))
        assert len(parsed) == 2
        assert parsed[0]["request"]["endpoint"] == "https://example.com/a"

    def test_reads_json_array(self, tmp_path) -> None:
        path = tmp_path / "crawl.json"
        path.write_text(json.dumps([make_record(), make_record("https://example.com/b")], indent=2), encoding="utf-8")

        assert len(list(iter_katana_records(path))) == 2

    def test_reads_pretty_printed_single_object(self, tmp_path) -> None:
        path = tmp_path / "one.json"
        path.write_text(json.dumps(make_record(), indent=2), encoding="utf-8")

        records = list(iter_katana_records(path))
        assert len(records) == 1

    def test_skips_malformed_lines(self, tmp_path) -> None:
        path = tmp_path / "crawl.jsonl"
        path.write_text(
            json.dumps(make_record("https://example.com/a")) + "\n{not json}\n" + json.dumps(make_record("https://example.com/b")),
            encoding="utf-8",
        )

        records = list(iter_katana_records(path))
        assert len(records) == 2

    def test_empty_file(self, tmp_path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        assert list(iter_katana_records(path)) == []
