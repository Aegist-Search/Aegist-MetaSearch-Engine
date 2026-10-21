"""Measure incremental ingestion and near-duplicate filtering on a Katana crawl.

The script runs the **real** pipeline (``app.ingestion.pipeline``) against an
in-memory Meilisearch stand-in, so it needs no database, writes nothing and can
be run on any crawl file:

    cd backend
    python scripts/measure_ingestion.py --input ../data/katana.jsonl

It reports, per pass:

* records read, documents prepared, skipped, same-URL duplicates;
* how many near-duplicates the SimHash filter removed (and the payload it
  saved), before any upload happens;
* on a second pass over the same crawl, how many documents are reported as
  unchanged and therefore never uploaded.

With ``--previous old.jsonl`` the first pass seeds the store from another
crawl, which is the real periodic-refresh scenario: the numbers describe what
a new crawl of the same site would upload.

``--meili`` additionally checks the live index (read-only) to say how many
documents already carry a ``content_hash`` and how many would be uploaded.

``--show-dropped N`` lists up to N near-duplicate pairs with their similarity,
using the same batches and threshold as the pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.ingestion.dedup import dedupe_documents, document_hash, similarity  # noqa: E402
from app.ingestion.katana import extract_document, iter_katana_records  # noqa: E402
from app.ingestion.pipeline import IngestionReport, ingest_katana_file  # noqa: E402

DEFAULT_BATCH_SIZE = 200


class MemoryMeili:
    """Minimal stand-in for the admin Meilisearch client.

    `ingest_katana_file` builds the client per run, so the store lives on the
    class and survives across passes.
    """

    documents: dict[str, dict[str, Any]] = {}
    uploaded_documents = 0
    uploaded_bytes = 0
    lookups = 0

    def __init__(self, *, base_url: str, master_key: str, index: str) -> None:
        self.index = index

    def __enter__(self) -> "MemoryMeili":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def ensure_index(self) -> bool:
        return True

    def apply_settings(self, settings: dict[str, Any]) -> None:
        pass

    def delete_all_documents(self) -> None:
        type(self).documents.clear()

    def add_documents(self, documents: list[dict[str, Any]]) -> int:
        for document in documents:
            payload = json.dumps(document, ensure_ascii=False, sort_keys=True)
            type(self).uploaded_documents += 1
            type(self).uploaded_bytes += len(payload.encode("utf-8"))
            type(self).documents[document["id"]] = dict(document)
        return len(documents)

    def document_hashes(self, ids: list[str]) -> dict[str, str]:
        type(self).lookups += 1
        return {
            document_id: document["content_hash"]
            for document_id, document in type(self).documents.items()
            if document_id in ids
        }

    @classmethod
    def snapshot(cls) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in cls.documents.items()}

    @classmethod
    def reset(cls) -> None:
        cls.documents = {}
        cls.uploaded_documents = 0
        cls.uploaded_bytes = 0
        cls.lookups = 0


@dataclass(frozen=True, slots=True)
class RunStats:
    uploaded_documents: int
    uploaded_bytes: int
    lookup_requests: int


def measure(
    path: Path,
    *,
    batch_size: int,
    limit: int,
    dedupe: bool,
    threshold: float,
    prefix_chars: int,
    reset: bool = False,
) -> tuple[IngestionReport, RunStats]:
    if reset:
        MemoryMeili.reset()

    before_documents = MemoryMeili.uploaded_documents
    before_bytes = MemoryMeili.uploaded_bytes
    before_lookups = MemoryMeili.lookups

    with patch("app.ingestion.pipeline.MeiliClient", MemoryMeili):
        report = ingest_katana_file(
            path,
            index="measure",
            meili_url="memory://",
            batch_size=batch_size,
            limit=limit,
            dedupe=dedupe,
            dedupe_threshold=threshold,
            dedupe_prefix_chars=prefix_chars,
            # The in-memory store starts empty unless --previous seeded it.
            skip_unchanged=True,
        )

    return report, RunStats(
        uploaded_documents=MemoryMeili.uploaded_documents - before_documents,
        uploaded_bytes=MemoryMeili.uploaded_bytes - before_bytes,
        lookup_requests=MemoryMeili.lookups - before_lookups,
    )


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"


def percentage(part: int, total: int) -> str:
    if total <= 0:
        return "n/a"
    return f"{part / total * 100:.1f}%"


def print_pass(title: str, report: IngestionReport, stats: RunStats) -> None:
    print(f"\n{title}")
    rows = [
        ("records read", f"{report.records}"),
        ("documents prepared", f"{report.documents}"),
        ("skipped (not HTML / not 2xx)", f"{report.skipped}"),
        ("duplicates (same canonical URL)", f"{report.duplicates}"),
        (
            "near-duplicates dropped",
            f"{report.near_duplicates} ({percentage(report.near_duplicates, report.documents)} of prepared)",
        ),
        ("uploaded to the index", f"{report.indexed}"),
        ("uploaded payload", human_bytes(stats.uploaded_bytes)),
        ("unchanged (skipped upload)", f"{report.unchanged}"),
        ("hash lookups", f"{stats.lookup_requests}"),
        ("elapsed", f"{report.elapsed_s:.2f} s"),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)}  {value}")


def dedup_pairs(
    path: Path,
    *,
    batch_size: int,
    threshold: float,
    prefix_chars: int,
    limit: int,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """Replay the pipeline batching to pair each dropped document with its keeper."""

    pairs: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    batch: list[dict[str, Any]] = []
    seen = 0

    def compare(documents: list[dict[str, Any]]) -> None:
        if len(documents) < 2:
            return

        result = dedupe_documents(
            documents,
            threshold=threshold,
            prefix_chars=prefix_chars,
        )
        kept_ids = {document["id"] for document in result.documents}
        for document in documents:
            if document["id"] in kept_ids:
                continue
            fingerprint = document_hash(document, prefix_chars=prefix_chars)
            keeper = max(
                result.documents,
                key=lambda candidate: similarity(
                    fingerprint,
                    document_hash(candidate, prefix_chars=prefix_chars),
                ),
            )
            score = similarity(
                fingerprint,
                document_hash(keeper, prefix_chars=prefix_chars),
            )
            pairs.append((keeper, document, score))

    for record in iter_katana_records(path):
        document = extract_document(record)
        if document is None:
            continue
        batch = [item for item in batch if item["id"] != document["id"]]
        batch.append(document)
        seen += 1
        if len(batch) >= max(1, batch_size):
            compare(batch)
            batch = []
        if limit and seen >= limit:
            break

    compare(batch)
    pairs.sort(key=lambda entry: entry[2], reverse=True)
    return pairs


def live_index_check(
    documents: dict[str, dict[str, Any]],
    *,
    settings: Any,
) -> None:
    from app.ingestion.meili import MeiliClient, MeiliIngestError

    if not documents:
        return

    ids = list(documents)
    try:
        with MeiliClient(
            base_url=settings.meili_url,
            master_key=settings.meili_master_key,
            index=settings.meili_index,
        ) as client:
            stored = client.document_hashes(ids)
    except MeiliIngestError as exc:
        print(f"\nLive index '{settings.meili_index}' at {settings.meili_url}: {exc}")
        if exc.status == 400:
            print("  hint: run the ingestion once to apply filterableAttributes (id).")
        return

    missing = [document_id for document_id in ids if document_id not in stored]
    unchanged = [
        document_id
        for document_id, document in documents.items()
        if stored.get(document_id) == document.get("content_hash")
    ]
    changed = len(ids) - len(missing) - len(unchanged)

    print(f"\nLive index '{settings.meili_index}' at {settings.meili_url} (read-only)")
    print(f"  documents checked        {len(ids)}")
    print(f"  not indexed yet          {len(missing)}")
    print(f"  identical hash           {len(unchanged)}")
    print(f"  changed                  {changed}")
    if missing:
        print(
            "  note: documents missing from the index (new, or indexed before "
            "content_hash existed) count as changed; one ingestion run without "
            "--replace populates the hash for the latter."
        )


def build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="aegis-measure",
        description=(
            "Measure near-duplicate filtering and incremental ingestion on a "
            "Katana crawl, without touching Meilisearch."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python scripts/measure_ingestion.py --input ../data/katana.jsonl\n"
            "  python scripts/measure_ingestion.py --input ../data/katana.jsonl --show-dropped 10\n"
            "  python scripts/measure_ingestion.py --previous ../data/old.jsonl "
            "--input ../data/katana.jsonl --meili\n"
        ),
    )
    parser.add_argument("-i", "--input", required=True, help="Katana JSONL of the new crawl")
    parser.add_argument(
        "--previous",
        default="",
        help="Earlier crawl used to seed the index before measuring the new one",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Documents per batch (default {DEFAULT_BATCH_SIZE}, same as the pipeline)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after N documents (0 = all)",
    )
    parser.add_argument(
        "--dedupe-threshold",
        type=float,
        default=settings.dedupe_threshold,
        help=f"SimHash similarity in (0, 1] (default {settings.dedupe_threshold})",
    )
    parser.add_argument(
        "--dedupe-prefix-chars",
        type=int,
        default=settings.dedupe_prefix_chars,
        help=f"Content characters hashed by SimHash (default {settings.dedupe_prefix_chars})",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Disable the near-duplicate filter to see the baseline",
    )
    parser.add_argument(
        "--show-dropped",
        type=int,
        default=0,
        metavar="N",
        help="List up to N near-duplicate pairs (kept / dropped, similarity)",
    )
    parser.add_argument(
        "--meili",
        action="store_true",
        help="Also compare with the live index from MEILI_URL (read-only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    dedupe = settings.dedupe_enabled and not args.no_dedupe

    input_path = Path(args.input).expanduser()
    if not input_path.is_file():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2

    previous_path: Path | None = None
    if args.previous:
        previous_path = Path(args.previous).expanduser()
        if not previous_path.is_file():
            print(f"Previous crawl not found: {previous_path}", file=sys.stderr)
            return 2

    if not 0 < args.dedupe_threshold <= 1:
        print("--dedupe-threshold must be in (0, 1]", file=sys.stderr)
        return 2

    print("Aegis ingestion measurement")
    print(f"  input      {input_path} ({human_bytes(input_path.stat().st_size)})")
    if previous_path is not None:
        print(f"  previous   {previous_path} ({human_bytes(previous_path.stat().st_size)})")
    print(f"  batch      {args.batch_size} documents")
    print(
        "  dedupe     "
        + (
            f"on (threshold {args.dedupe_threshold:.2f}, prefix {args.dedupe_prefix_chars} chars)"
            if dedupe
            else "off"
        )
    )

    common: dict[str, Any] = {
        "batch_size": args.batch_size,
        "limit": args.limit,
        "dedupe": dedupe,
        "threshold": args.dedupe_threshold,
        "prefix_chars": args.dedupe_prefix_chars,
    }

    if previous_path is not None:
        seed, seed_stats = measure(previous_path, reset=True, **common)
        print_pass(f"Seed pass — {previous_path.name} (populates the index)", seed, seed_stats)
        first, first_stats = measure(input_path, **common)
        title = f"New crawl — {input_path.name} against the seeded index"
    else:
        first, first_stats = measure(input_path, reset=True, **common)
        title = f"Pass 1 — {input_path.name} into an empty index"

    print_pass(title, first, first_stats)

    if first.documents == 0:
        print(
            "\nNo documents were extracted: check that the crawl is HTML and was "
            "produced without Katana's -omit-body flag.",
            file=sys.stderr,
        )
        return 1

    second, second_stats = measure(input_path, **common)
    print_pass(f"Pass 2 — {input_path.name} against the populated index", second, second_stats)

    candidates = second.unchanged + second.indexed
    print("\nIncremental effect")
    print(f"  documents compared       {candidates}")
    print(
        f"  already up to date       {second.unchanged} "
        f"({percentage(second.unchanged, candidates)})"
    )
    print(f"  would be uploaded        {second.indexed}")
    print(
        "  upload avoided           "
        f"{human_bytes(first_stats.uploaded_bytes - second_stats.uploaded_bytes)} "
        "of payload on the second run"
    )

    print("\nNear-duplicate effect")
    unique = first.documents - first.near_duplicates
    print(f"  prepared                 {first.documents}")
    print(f"  kept after dedupe        {unique}")
    print(
        f"  removed                  {first.near_duplicates} "
        f"({percentage(first.near_duplicates, first.documents)} of prepared)"
    )

    if args.show_dropped > 0 and dedupe:
        pairs = dedup_pairs(
            input_path,
            batch_size=args.batch_size,
            threshold=args.dedupe_threshold,
            prefix_chars=args.dedupe_prefix_chars,
            limit=args.limit,
        )
        print(f"\nNear-duplicate pairs (showing {min(len(pairs), args.show_dropped)} of {len(pairs)})")
        for keeper, dropped, score in pairs[: args.show_dropped]:
            print(f"  {score * 100:5.1f}%  kept     {keeper['url']}")
            print(f"           dropped  {dropped['url']}")
        if not pairs:
            print("  none: no two documents in the same batch were similar enough")

    if args.meili:
        live_index_check(MemoryMeili.snapshot(), settings=settings)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
