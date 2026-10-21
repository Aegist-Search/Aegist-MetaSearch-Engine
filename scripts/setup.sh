#!/usr/bin/env sh
#
# Aegis Search - setup (Linux / macOS)
#
# Copies the .env templates next to their destination files without
# overwriting anything and creates data/.
#
# Usage: ./scripts/setup.sh

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

copy_template() {
    src=$1
    dest=$2

    if [ -e "$dest" ]; then
        printf 'SKIP  %s already exists\n' "$dest"
        return 0
    fi

    if [ ! -f "$src" ]; then
        printf 'ERR   template not found: %s\n' "$src" >&2
        exit 1
    fi

    cp "$src" "$dest"
    printf 'OK    %s created\n' "$dest"
}

printf 'Aegis Search - setup\n\n'

copy_template .env.example .env
copy_template backend/.env.example backend/.env
copy_template frontend/.env.example frontend/.env.local

mkdir -p data
printf 'OK    data/ ready\n'

cat <<'EOF'

Next steps:
  1. Edit .env and set MEILI_MASTER_KEY and BRAVE_API_KEY.
     Optional: EXA_API_KEY, SERPER_API_KEY, TAVILY_API_KEY for the shield.
  2. Start the stack:  ./scripts/start.sh
  3. Backend health:   curl http://127.0.0.1:8000/api/v1/health
  4. Frontend:         http://127.0.0.1:3000
EOF
