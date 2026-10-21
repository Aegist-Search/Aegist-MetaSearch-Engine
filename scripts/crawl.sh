#!/usr/bin/env sh
#
# Aegis Search - crawl (Linux / macOS)
#
# * runs projectdiscovery/katana:latest in Docker, mounting data/ at /data, so
#   the JSONL always lands in data/ and never in the project root
# * checks that the file exists, is not empty and carries the HTML body
# * with --ingest runs the ingestion pipeline inside the backend container
#
# Usage:
#   ./scripts/crawl.sh --url https://example.com [options] [-- extra katana args]
#
# Options:
#   --url URL          starting URL (required)
#   --depth N          katana -d (default 3)
#   --rate-limit N     katana -rl (default 20)
#   --timeout N        katana -timeout, seconds (default 10)
#   --retry N          katana -retry (default 2)
#   --output PATH      file inside data/ (default data/katana.jsonl)
#   --headless         add -system-chrome -headless for JavaScript sites
#   --ingest           run the ingestion pipeline afterwards
#   --replace          with --ingest, replace the existing documents

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DATA_DIR="$ROOT/data"

URL=""
DEPTH=3
RATE=20
TIMEOUT=10
RETRY=2
OUTPUT="data/katana.jsonl"
HEADLESS=0
INGEST=0
REPLACE=0

warn() { printf 'WARN  %s\n' "$*"; }
step() { printf '==> %s\n' "$*"; }
ok()   { printf 'OK    %s\n' "$*"; }
fail() { printf 'ERR   %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: ./scripts/crawl.sh --url URL [options] [-- extra katana args]

  --url URL          starting URL (required)
  --depth N          katana -d (default 3)
  --rate-limit N     katana -rl (default 20)
  --timeout N        katana -timeout, seconds (default 10)
  --retry N          katana -retry (default 2)
  --output PATH      file inside data/ (default data/katana.jsonl)
  --headless         add -system-chrome -headless for JavaScript sites
  --ingest           run the ingestion pipeline afterwards
  --replace          with --ingest, replace the existing documents
  -h, --help         show this help
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --url|-Url)             URL=${2:?"$1 needs a value"}; shift 2 ;;
        --depth|-Depth)         DEPTH=${2:?"$1 needs a value"}; shift 2 ;;
        --rate-limit|-RateLimit) RATE=${2:?"$1 needs a value"}; shift 2 ;;
        --timeout|-Timeout)     TIMEOUT=${2:?"$1 needs a value"}; shift 2 ;;
        --retry|-Retry)         RETRY=${2:?"$1 needs a value"}; shift 2 ;;
        --output|-Output)       OUTPUT=${2:?"$1 needs a value"}; shift 2 ;;
        --headless|-Headless)   HEADLESS=1; shift ;;
        --ingest|-Ingest)       INGEST=1; shift ;;
        --replace|-Replace)     REPLACE=1; shift ;;
        -h|--help)              usage; exit 0 ;;
        --)                     shift; break ;;
        *)                      printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done
# Whatever is left is forwarded to katana verbatim.

[ -n "$URL" ] || fail "a --url is required (try --help)"
case "$URL" in
    http://*|https://*) ;;
    *) fail "the URL must start with http:// or https:// (got: $URL)" ;;
esac

for pair in "depth:$DEPTH" "rate-limit:$RATE" "timeout:$TIMEOUT" "retry:$RETRY"; do
    name=${pair%%:*}
    value=${pair#*:}
    case "$value" in
        ''|*[!0-9]*) fail "--$name must be a number, got: $value" ;;
    esac
done

# The output must stay inside data/, which is the only folder mounted in /data.
case "$OUTPUT" in
    data/*|./data/*) rel=${OUTPUT#./}; rel=${rel#data/} ;;
    *) fail "the output must stay inside data/, got: $OUTPUT" ;;
esac
[ -n "$rel" ] || fail "name a file, not the data/ folder: --output data/katana.jsonl"

command -v docker >/dev/null 2>&1 || fail "docker not found in PATH"

mkdir -p "$DATA_DIR/$(dirname "$rel")"

full_output="$DATA_DIR/$rel"
container_output="/data/$rel"
full_temp="$full_output.tmp"
container_temp="$container_output.tmp"

if [ -e "$full_temp" ]; then
    rm -f "$full_temp"
fi

step "Crawl $URL (depth $DEPTH, $RATE req/s)"
printf '    output: %s\n' "$full_output"

# Katana writes to a temporary file so a failed crawl leaves the previous JSONL
# untouched and two crawls can never be mixed.
if [ "$HEADLESS" -eq 1 ]; then
    docker run --rm \
        -v "$DATA_DIR:/data" \
        projectdiscovery/katana:latest \
        -u "$URL" -d "$DEPTH" -rl "$RATE" -timeout "$TIMEOUT" -retry "$RETRY" \
        -system-chrome -headless -jsonl -o "$container_temp" "$@"
else
    docker run --rm \
        -v "$DATA_DIR:/data" \
        projectdiscovery/katana:latest \
        -u "$URL" -d "$DEPTH" -rl "$RATE" -timeout "$TIMEOUT" -retry "$RETRY" \
        -jsonl -o "$container_temp" "$@"
fi || fail "katana failed"

[ -f "$full_temp" ] || fail "katana did not create $full_temp (check the URL and the mount)"

records=$(wc -l <"$full_temp" | tr -d ' ')
records=${records:-0}
if [ "$records" -eq 0 ]; then
    rm -f "$full_temp"
    fail "the crawl is empty: katana wrote no line"
fi

mv -f "$full_temp" "$full_output"
ok "$records records written to $full_output"

if head -n 1 "$full_output" | grep -q '"body"'; then
    ok "HTML body present in the first record"
else
    warn "the first record has no response.body: the pipeline cannot extract title and text (do not use -omit-body)"
fi

if [ "$INGEST" -eq 1 ]; then
    step "Ingest into Meilisearch"
    if [ "$REPLACE" -eq 1 ]; then
        docker compose exec -T backend python -m app.ingestion.cli --input "$container_output" --replace \
            || fail "ingestion failed (is the backend up? try: docker compose up -d --build backend)"
    else
        docker compose exec -T backend python -m app.ingestion.cli --input "$container_output" \
            || fail "ingestion failed (is the backend up? try: docker compose up -d --build backend)"
    fi
    ok "Index updated from $container_output"
else
    printf '\nTo index the crawl:\n'
    printf '  docker compose exec backend python -m app.ingestion.cli --input %s --replace\n' "$container_output"
    printf 'or rerun this script with --ingest --replace\n'
fi
