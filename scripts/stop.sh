#!/usr/bin/env sh
#
# Aegis Search - stop (Linux / macOS)
#
# * ends the frontend started by start.sh (PID in logs/frontend.pid)
# * falls back to the process listening on the port when the PID is gone
# * stops the containers (docker compose stop, down or down --volumes)
#
# Usage:
#   ./scripts/stop.sh [--port 3000] [--down] [--volumes]

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PIDFILE="$ROOT/logs/frontend.pid"

PORT=3000
DOWN=0
VOLUMES=0

usage() {
    cat <<'EOF'
Usage: ./scripts/stop.sh [options]

  --port N     frontend port used for the fallback (default 3000)
  --down       docker compose down: removes containers and network, keeps volumes
  --volumes    docker compose down --volumes: also deletes the index
  -h, --help   show this help
EOF
}

warn() { printf 'WARN  %s\n' "$*"; }
step() { printf '==> %s\n' "$*"; }
ok()   { printf 'OK    %s\n' "$*"; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --port|-Port)       PORT=${2:?"$1 needs a value"}; shift 2 ;;
        --down|-Down)       DOWN=1; shift ;;
        --volumes|-Volumes) VOLUMES=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *)                  printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

case "$PORT" in
    ''|*[!0-9]*) printf 'ERR   port must be a number, got: %s\n' "$PORT" >&2; exit 2 ;;
esac

# Prints the PID listening on the given TCP port, if it can be determined.
port_pid() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -n 1
        return 0
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -lptn "sport = :$1" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | head -n 1
        return 0
    fi
    if command -v fuser >/dev/null 2>&1; then
        fuser "$1/tcp" 2>/dev/null | tr -s ' ' '\n' | head -n 1
        return 0
    fi
    return 0
}

step "Stop the frontend"

stopped=0

if [ -f "$PIDFILE" ]; then
    saved_pid=$(tr -dc '0-9' <"$PIDFILE")
    if [ -n "$saved_pid" ] && kill -0 "$saved_pid" 2>/dev/null; then
        pkill -TERM -P "$saved_pid" 2>/dev/null || true
        kill -TERM "$saved_pid" 2>/dev/null || true
        stopped=1
        ok "Frontend stopped (PID $saved_pid)"
    fi
    rm -f "$PIDFILE"
fi

listener=$(port_pid "$PORT")
if [ -n "$listener" ]; then
    name=$(ps -p "$listener" -o comm= 2>/dev/null || true)
    case "$name" in
        *node*)
            pkill -TERM -P "$listener" 2>/dev/null || true
            kill -TERM "$listener" 2>/dev/null || true
            stopped=1
            ok "node process on port $PORT stopped (PID $listener)"
            ;;
        *)
            warn "port $PORT is used by '${name:-unknown}' (PID $listener): leaving it alone"
            ;;
    esac
fi

if [ "$stopped" -eq 0 ]; then
    warn "no frontend started by these scripts is listening on port $PORT"
fi

step "Stop the backend (Docker Compose)"

if ! command -v docker >/dev/null 2>&1; then
    warn "docker not found in PATH: containers were not stopped"
    exit 1
fi

if [ "$VOLUMES" -eq 1 ]; then
    warn "removing the meili_data volume: the index must be rebuilt with a new ingestion"
    docker compose down --volumes || { warn "docker compose returned $?"; exit 1; }
    ok "Containers, network and index volume removed"
elif [ "$DOWN" -eq 1 ]; then
    docker compose down || { warn "docker compose returned $?"; exit 1; }
    ok "Containers and network removed (meili_data volume kept)"
else
    docker compose stop || { warn "docker compose returned $?"; exit 1; }
    ok "Containers stopped (volumes kept)"
fi

printf '\nAll stopped. Start again with ./scripts/start.sh\n'
