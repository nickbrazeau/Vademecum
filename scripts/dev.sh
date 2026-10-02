#!/usr/bin/env bash
#
# The one command. Starts Vademecum on this Mac and nothing else:
#
#   ./scripts/dev.sh            backend + web app, ready to use
#   ./scripts/dev.sh --check    set everything up, prove /health answers, exit
#   ./scripts/dev.sh --api-only backend only
#
# It creates the Python virtualenv and installs node modules the first time,
# applies any outstanding migrations, waits until /health actually answers, and
# only then starts the web app. If a step fails it stops and says which.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
VENV="$API_DIR/.venv"

API_HOST="${VADEMECUM_HOST:-127.0.0.1}"
API_PORT="${VADEMECUM_PORT:-8765}"
WEB_PORT="${VADEMECUM_WEB_PORT:-5173}"

MODE="full"
case "${1:-}" in
  --check) MODE="check" ;;
  --api-only) MODE="api" ;;
  "") ;;
  *)
    echo "usage: $0 [--check|--api-only]" >&2
    exit 64
    ;;
esac

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m==> %s\033[0m\n' "$*" >&2; exit 1; }

find_python() {
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

# Wait until either of two children stops. `wait -n` is Bash 4.3 and macOS ships
# Bash 3.2, so poll instead: return as soon as one child is gone and leave the
# survivor running for the EXIT trap to stop.
supervise() {
  while kill -0 "$1" 2>/dev/null && kill -0 "$2" 2>/dev/null; do
    sleep 1
  done
}

# --- backend -----------------------------------------------------------------

if [ ! -x "$VENV/bin/python" ]; then
  PYTHON="$(find_python)" || die "Python 3.12 or newer is required and was not found on PATH."
  say "Creating the virtualenv with $PYTHON"
  "$PYTHON" -m venv "$VENV"
  # `python -m pip`, never the console script: pip bakes an absolute
  # interpreter path into .venv/bin/* at install time, which breaks the moment
  # the checkout moves (ADR 0005).
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -e "$API_DIR[dev]"
  # The MCP server (scripts/mcp.sh) shares this virtualenv. Installed here so
  # a fresh checkout has it; mcp.sh installs it into an older venv on demand.
  "$VENV/bin/python" -m pip install --quiet -e "$ROOT/apps/mcp"
fi

# --- web ---------------------------------------------------------------------

# The launcher runs Vite's own binary, so that is what has to be present: a
# node_modules directory left half-installed would otherwise look ready.
if [ "$MODE" = "full" ] && [ ! -x "$WEB_DIR/node_modules/.bin/vite" ]; then
  command -v npm >/dev/null 2>&1 || die "npm is required for the web app and was not found on PATH."
  say "Installing node modules"
  (cd "$WEB_DIR" && npm install --no-audit --no-fund)
fi

# --- run ---------------------------------------------------------------------

API_PID=""
WEB_PID=""
cleanup() {
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

say "Starting the local API on http://$API_HOST:$API_PORT"
# `exec` so that $! is the server itself. Without it the recorded pid is the
# subshell wrapping the cd, and `kill` on that wrapper leaves the server it
# started running and still holding the port.
(cd "$API_DIR" && exec "$VENV/bin/python" -m vademecum) &
API_PID=$!

say "Waiting for /health"
for attempt in $(seq 1 60); do
  if curl -fsS "http://$API_HOST:$API_PORT/api/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    die "The API stopped while starting. Its error is above."
  fi
  if [ "$attempt" -eq 60 ]; then
    die "The API did not answer /health within 30 seconds."
  fi
  sleep 0.5
done

curl -fsS "http://$API_HOST:$API_PORT/api/health"
echo

if [ "$MODE" = "check" ]; then
  say "Everything is in place."
  exit 0
fi

if [ "$MODE" = "api" ]; then
  say "API only. Ctrl-C to stop."
  wait "$API_PID"
  exit 0
fi

say "Starting the web app on http://127.0.0.1:$WEB_PORT"
# Vite directly rather than `npm run dev`: npm is one more layer between the
# recorded pid and the server, and it does not pass a terminating signal on to
# what it started. This is exactly the `dev` script in apps/web/package.json.
(cd "$WEB_DIR" && exec "$WEB_DIR/node_modules/.bin/vite" --port "$WEB_PORT") &
WEB_PID=$!

say "Open http://127.0.0.1:$WEB_PORT — Ctrl-C to stop both."
supervise "$API_PID" "$WEB_PID"
die "One of the two processes stopped. See the output above."
