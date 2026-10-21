"""Command line entrypoint for the Katana → Meilisearch pipeline.

Usage (from the ``backend/`` directory):

    python -m app.ingestion.cli --input ../data/katana.jsonl
    python -m app.ingestion.cli --input ../data/katana.jsonl --replace
    python -m app.ingestion.cli --input ../data/katana.jsonl --dry-run
    python -m app.ingestion.cli --input ../data/katana.jsonl --no-skip-unchanged

By default only documents whose content changed since the previous crawl are
uploaded, and near-duplicate pages inside a batch keep their newest variant.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.ingestion.katana import KatanaFormatError
from app.ingestion.meili import MeiliIngestError
from app.ingestion.pipeline import ingest_katana_file

logger = logging.getLogger("aegis.ingest")


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("expected a non-negative integer")
    return parsed


def _threshold(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a number") from exc
    if not 0 < parsed <= 1:
        raise argparse.ArgumentTypeError("expected a similarity in (0, 1]")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aegis-ingest",
        description="Convert a Katana JSONL crawl into Meilisearch documents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m app.ingestion.cli --input ../data/katana.jsonl\n"
            "  python -m app.ingestion.cli --input ../data/katana.jsonl --replace\n"
            "  python -m app.ingestion.cli --input ../data/katana.jsonl --dry-run --limit 5\n"
        ),
    )
    parser.add_argument("-i", "--input", required=True, help="Katana JSONL (or JSON) output file")
    parser.add_argument("--index", default="", help="Target Meilisearch index (default: MEILI_INDEX)")
    parser.add_argument("--meili-url", default="", help="Override MEILI_URL")
    parser.add_argument("--batch-size", type=_positive_int, default=200, help="Documents per index request")
    parser.add_argument("--limit", type=_non_negative_int, default=0, help="Stop after N documents (0 = all)")
    parser.add_argument("--replace", action="store_true", help="Delete existing documents before indexing")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not contact Meilisearch")
    parser.add_argument(
        "--allow-empty-body",
        action="store_true",
        help="Index pages without an HTML body (title/snippet fall back to the URL)",
    )
    parser.add_argument(
        "--skip-unchanged",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Upload only the documents whose content changed since the previous "
            "crawl (default: on; use --no-skip-unchanged to upload everything)"
        ),
    )
    parser.add_argument(
        "--dedupe",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Drop near-duplicate pages inside a batch (default: DEDUPE_ENABLED)",
    )
    parser.add_argument(
        "--dedupe-threshold",
        type=_threshold,
        default=None,
        help="SimHash similarity in (0, 1] above which two pages match (default: DEDUPE_THRESHOLD)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    return parser


def _preview(document: dict[str, Any]) -> dict[str, Any]:
    content = document.get("content") or ""
    return {**document, "content": content[:240] + ("…" if len(content) > 240 else "")}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s | %(message)s",
    )

    settings = get_settings()
    input_path = Path(args.input).expanduser()
    if not input_path.is_file():
        logger.error("Input file not found: %s", input_path)
        return 2

    index = args.index or settings.meili_index
    meili_url = args.meili_url or settings.meili_url
    dedupe = settings.dedupe_enabled if args.dedupe is None else args.dedupe
    dedupe_threshold = (
        settings.dedupe_threshold if args.dedupe_threshold is None else args.dedupe_threshold
    )

    if not args.dry_run:
        logger.info("Preparing Meilisearch index '%s'...", index)
        if args.replace:
            logger.info("Deleting existing documents...")

    try:
        report = ingest_katana_file(
            input_path,
            index=index,
            meili_url=meili_url,
            master_key=settings.meili_master_key,
            batch_size=args.batch_size,
            limit=args.limit,
            replace=args.replace,
            dry_run=args.dry_run,
            allow_empty_body=args.allow_empty_body,
            dedupe=dedupe,
            dedupe_threshold=dedupe_threshold,
            dedupe_prefix_chars=settings.dedupe_prefix_chars,
            skip_unchanged=args.skip_unchanged,
            sample_size=3 if args.dry_run else 0,
            on_batch=(
                None
                if args.dry_run
                else lambda indexed, documents: logger.info(
                    "Indexed %d/%d documents...", indexed, documents
                )
            ),
        )
    except (MeiliIngestError, KatanaFormatError, OSError) as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logger.error("Interrupted.")
        return 130

    summary = (
        "%d records read | %d documents prepared | %d unchanged | %d skipped | "
        "%d duplicates | %d near-duplicates | %.1fs"
        % (
            report.records,
            report.documents,
            report.unchanged,
            report.skipped,
            report.duplicates,
            report.near_duplicates,
            report.elapsed_s,
        )
    )

    if args.dry_run:
        logger.info("Dry run: %s", summary)
        for sample in report.samples:
            print(json.dumps(_preview(sample), ensure_ascii=False, indent=2))
    else:
        logger.info("Indexed %d documents into '%s' (%s)", report.indexed, index, summary)

    if report.documents == 0:
        logger.error(
            "No documents were extracted. Check that the crawl is HTML and was produced "
            "without Katana's -omit-body flag, then try again."
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
