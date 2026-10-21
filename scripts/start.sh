#!/usr/bin/env sh
#
# Aegis Search - start (Linux / macOS)
#
# * starts Meilisearch, Tor and FastAPI with Docker Compose
# * waits for the backend health endpoint
# * starts `npm run dev` in the background (logs in logs/, PID in logs/frontend.pid)
# * waits for the frontend port and prints the URLs
#
# Usage:
#   ./scripts/start.sh [--port 3000] [--timeout 120] [--no-build] [--stop]
#
# --stop is a shortcut for `stop.sh --port N`: it stops the frontend and the
# containers, then exits (nothing is started).

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
FRONTEND="$ROOT/frontend"
LOGDIR="$ROOT/logs"
PIDFILE="$LOGDIR/frontend.pid"
STDOUT_LOG="$LOGDIR/frontend.out.log"
STDERR_LOG="$LOGDIR/frontend.err.log"
BACKEND_HEALTH="http://127.0.0.1:8000/api/v1/health"

PORT=3000
TIMEOUT=120
NOBUILD=0
STOP=0

usage() {
    cat <<'EOF'
Usage: ./scripts/start.sh [options]

  --port N        frontend port (default 3000)
  --timeout N     seconds to wait for backend and frontend (default 120)
  --no-build      do not rebuild the backend image
  --stop          run stop.sh --port N (frontend + containers) and exit
  -h, --help      show this help
EOF
}

warn() { printf 'WARN  %s\n' "$*"; }
step() { printf '==> %s\n' "$*"; }
ok()   { printf 'OK    %s\n' "$*"; }
fail() { printf 'ERR   %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --port|-Port)       PORT=${2:?"$1 needs a value"}; shift 2 ;;
        --timeout|-Timeout) TIMEOUT=${2:?"$1 needs a value"}; shift 2 ;;
        --no-build|-NoBuild) NOBUILD=1; shift ;;
        --stop|-Stop)       STOP=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *)                  printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$PORT" in
    ''|*[!0-9]*) fail "port must be a number, got: $PORT" ;;
esac
case "$TIMEOUT" in
    ''|*[!0-9]*) fail "timeout must be a number, got: $TIMEOUT" ;;
esac

if [ "$STOP" -eq 1 ]; then
    exec sh "$ROOT/scripts/stop.sh" --port "$PORT"
fi

command -v docker >/dev/null 2>&1 || fail "docker not found in PATH"
command -v npm >/dev/null 2>&1 || fail "npm not found in PATH (Node.js 20+)"
command -v curl >/dev/null 2>&1 || fail "curl not found in PATH (used for the readiness checks)"

mkdir -p "$ROOT/data" "$LOGDIR"

step "Start the stack (docker compose up -d)"

if [ "$NOBUILD" -eq 1 ]; then
    docker compose up -d || fail "docker compose up failed"
else
    docker compose up -d --build || fail "docker compose up --build failed"
fi
ok "Containers started"

if [ ! -f "$ROOT/.env" ]; then
    warn ".env not found: run ./scripts/setup.sh first and fill in the keys"
fi

http_ok() {
    curl -fsS -o /dev/null --max-time 3 "$1" >/dev/null 2>&1
}

port_open() {
    curl -sS -o /dev/null --max-time 2 "$1" >/dev/null 2>&1
}

step "Wait for the backend on $BACKEND_HEALTH"

waited=0
backend_ready=0
while [ "$waited" -lt "$TIMEOUT" ]; do
    if http_ok "$BACKEND_HEALTH"; then
        backend_ready=1
        break
    fi
    sleep 1
    waited=$((waited + 1))
done

if [ "$backend_ready" -eq 1 ]; then
    ok "Backend ready: http://127.0.0.1:8000/docs"
else
    warn "backend not reachable after ${TIMEOUT}s (check: docker compose logs backend)"
fi

FRONTEND_URL="http://127.0.0.1:$PORT"
if port_open "$FRONTEND_URL"; then
    warn "port $PORT is already in use: the frontend is not started again"
else
    if [ ! -d "$FRONTEND/node_modules" ]; then
        step "Install frontend dependencies (npm install)"
        ( cd "$FRONTEND" && npm install --no-audit --no-fund ) || fail "npm install failed"
        ok "Dependencies installed"
    fi

    step "Start the frontend on port $PORT (logs in logs/)"
    : >"$STDOUT_LOG"
    : >"$STDERR_LOG"

    # nohup + redirect: the process survives this shell and writes to the logs.
    # The subshell records the PID of the npm process so stop.sh can end it.
    ( cd "$FRONTEND" && nohup npm run dev -- --port "$PORT" >"$STDOUT_LOG" 2>"$STDERR_LOG" & echo $! >"$PIDFILE" )

    waited=0
    frontend_ready=0
    while [ "$waited" -lt "$TIMEOUT" ]; do
        if port_open "$FRONTEND_URL"; then
            frontend_ready=1
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    if [ "$frontend_ready" -ne 1 ]; then
        warn "the frontend did not open port $PORT; last log lines:"
        tail -n 15 "$STDERR_LOG" 2>/dev/null || true
        tail -n 15 "$STDOUT_LOG" 2>/dev/null || true
        fail "frontend did not start (see logs/)"
    fi
    ok "Frontend ready: $FRONTEND_URL"
fi

printf '\nAegis Search is running\n'
printf '  Frontend   %s\n' "$FRONTEND_URL"
printf '  Backend    http://127.0.0.1:8000\n'
printf '  API docs   http://127.0.0.1:8000/docs\n'
printf '  Logs       logs/frontend.out.log, logs/frontend.err.log\n'
printf '  Stop       ./scripts/stop.sh\n'

if [ -f "$ROOT/data/katana.jsonl" ]; then
    printf '\nCrawl available: data/katana.jsonl\n'
    printf '  docker compose exec backend python -m app.ingestion.cli --input /data/katana.jsonl\n'
fi
