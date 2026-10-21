"""Katana JSONL → Meilisearch ingestion, reusable from the CLI and the API.

The CLI (``python -m app.ingestion.cli``) and the on-demand crawler
(``app.services.crawler_service``) must share exactly one pipeline, otherwise
the two paths would slowly diverge (different batches, different index
settings, different duplicate handling).

``ingest_katana_file`` is synchronous because the admin Meilisearch client is
synchronous: the crawler runs it in a worker thread so the event loop keeps
serving requests while documents are uploaded.

Two filters run before every upload batch:

* **near-duplicates** — SimHash over the content prefix (``app.ingestion.dedup``),
  so pagination/print variants do not take a slot each;
* **unchanged documents** — the stored ``content_hash`` is compared with the
  fresh one, so a periodic re-crawl only uploads pages that actually changed.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ingestion.dedup import (
    DEFAULT_PREFIX_CHARS,
    DEFAULT_THRESHOLD,
    dedupe_documents,
)
from app.ingestion.index_config import INDEX_SETTINGS
from app.ingestion.katana import extract_document, iter_katana_records
from app.ingestion.meili import MeiliClient, MeiliIngestError

DEFAULT_BATCH_SIZE = 200


@dataclass(frozen=True, slots=True)
class IngestionReport:
    """Counters for one Katana file, used by logs and by the crawler status."""

    records: int = 0
    documents: int = 0
    skipped: int = 0
    duplicates: int = 0
    near_duplicates: int = 0
    unchanged: int = 0
    indexed: int = 0
    elapsed_s: float = 0.0
    samples: tuple[dict[str, Any], ...] = ()


def ingest_katana_file(
    path: str | Path,
    *,
    index: str,
    meili_url: str,
    master_key: str = "",
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int = 0,
    replace: bool = False,
    dry_run: bool = False,
    allow_empty_body: bool = False,
    apply_settings: bool = True,
    dedupe: bool = True,
    dedupe_threshold: float = DEFAULT_THRESHOLD,
    dedupe_prefix_chars: int = DEFAULT_PREFIX_CHARS,
    skip_unchanged: bool = True,
    sample_size: int = 0,
    on_batch: Callable[[int, int], None] | None = None,
) -> IngestionReport:
    """Parse ``path`` and upsert the extracted documents into Meilisearch.

    Args:
        path: Katana JSONL file (or a single JSON array/object).
        index: Target Meilisearch index.
        meili_url: Meilisearch base URL.
        master_key: Optional admin API key.
        batch_size: Documents per index request.
        limit: Stop after N documents (``0`` means no limit).
        replace: Delete the existing documents before indexing.
        dry_run: Parse only, never contact Meilisearch.
        allow_empty_body: Index pages without an HTML body.
        apply_settings: Apply ``INDEX_SETTINGS`` on every run. The crawler
            leaves this false: settings are applied when the index is created,
            where they are written once instead of on every crawl.
        dedupe: Drop near-duplicate pages inside a batch, keeping the most
            recently crawled variant.
        dedupe_threshold: SimHash similarity (0..1] above which two pages are
            considered the same content.
        dedupe_prefix_chars: Characters of ``content`` compared by SimHash.
        skip_unchanged: Compare each batch with the stored ``content_hash`` and
            upload only the documents that changed. Disabled automatically by
            ``replace``, where the index is empty by definition.
        sample_size: Keep the first N parsed documents in the report (used by
            ``--dry-run`` to print a preview).
        on_batch: Optional progress callback ``(indexed, documents)``.

    Raises:
        MeiliIngestError: Meilisearch rejected a request.
        KatanaFormatError: The input file is not readable as Katana JSON/JSONL.
        OSError: The input file cannot be read or Meilisearch is unreachable.
    """

    file_path = Path(path)
    counters: Counter[str] = Counter()
    buffer: dict[str, dict[str, Any]] = {}
    samples: list[dict[str, Any]] = []
    indexed = 0
    started = time.perf_counter()
    # Comparing with the index only makes sense when the index survives this
    # run: ``--replace`` wipes it first, so every document is new.
    compare_with_index = skip_unchanged and not replace

    def flush(client: MeiliClient | None) -> None:
        nonlocal indexed
        if not buffer:
            return

        pending = list(buffer.values())
        buffer.clear()

        if dedupe and len(pending) > 1:
            deduped = dedupe_documents(
                pending,
                threshold=dedupe_threshold,
                prefix_chars=dedupe_prefix_chars,
            )
            counters["near_duplicates"] += deduped.dropped
            pending = deduped.documents

        if client is None:
            return

        if compare_with_index and pending:
            try:
                stored = client.document_hashes([document["id"] for document in pending])
            except MeiliIngestError as exc:
                # 400 = the filter was rejected: ``id`` is not filterable yet
                # (index created by an older version). Nothing is known to be
                # unchanged, so the batch is uploaded and the next run, after
                # the settings have been reapplied, gets the fast path.
                # Connectivity and auth errors must surface instead of
                # silently re-uploading everything.
                if exc.status != 400:
                    raise
                stored = {}

            changed = [
                document
                for document in pending
                if stored.get(document["id"]) != document.get("content_hash")
            ]
            counters["unchanged"] += len(pending) - len(changed)
            pending = changed

        if not pending:
            return

        indexed += client.add_documents(pending)
        if on_batch is not None:
            on_batch(indexed, counters["documents"])

    client_context: Any = (
        nullcontext(None)
        if dry_run
        else MeiliClient(base_url=meili_url, master_key=master_key, index=index)
    )

    with client_context as client:
        if client is not None:
            created = client.ensure_index()
            if created or apply_settings:
                client.apply_settings(INDEX_SETTINGS)
            if replace:
                client.delete_all_documents()

        for record in iter_katana_records(file_path):
            counters["records"] += 1
            document = extract_document(record, require_body=not allow_empty_body)
            if document is None:
                counters["skipped"] += 1
                continue

            if document["id"] in buffer:
                counters["duplicates"] += 1
            buffer[document["id"]] = document
            counters["documents"] += 1

            if sample_size and len(samples) < sample_size:
                samples.append(document)

            if len(buffer) >= max(1, batch_size):
                flush(client)

            if limit and counters["documents"] >= limit:
                break

        flush(client)

    return IngestionReport(
        records=counters["records"],
        documents=counters["documents"],
        skipped=counters["skipped"],
        duplicates=counters["duplicates"],
        near_duplicates=counters["near_duplicates"],
        unchanged=counters["unchanged"],
        indexed=indexed,
        elapsed_s=time.perf_counter() - started,
        samples=tuple(samples),
    )
