#!/usr/bin/env bash
#
# Vademecum for ChatGPT and Claude: the MCP server (ADR 0008).
#
#   ./scripts/mcp.sh                 HTTP mode on 127.0.0.1:8766; needs VADEMECUM_MCP_PUBLIC_URL
#   ./scripts/mcp.sh --tunnel        HTTP mode behind `tailscale funnel`, public URL worked out for you
#   ./scripts/mcp.sh --stdio         stdio mode, for Codex, Claude Desktop or Claude Code on this
#                                    Mac; starts the API itself if nothing is listening
#   ./scripts/mcp.sh setup codex     register the launcher with Codex (the ChatGPT app)
#   ./scripts/mcp.sh setup claude    register the launcher with Claude Desktop
#   ./scripts/mcp.sh setup folder [PATH]   choose the source folder (default ~/Documents/Vademecum)
#   ./scripts/mcp.sh passphrase      set or replace the passphrase that approves a connection
#   ./scripts/mcp.sh status          what is configured and who is connected
#   ./scripts/mcp.sh revoke-all      sign every assistant out
#   ./scripts/mcp.sh invite          multi tenancy: a one-time invite code for a new learner
#   ./scripts/mcp.sh learners        multi tenancy: list learners
#   ./scripts/mcp.sh disable HANDLE  multi tenancy: stop a learner signing in, revoke their tokens
#   ./scripts/mcp.sh reset HANDLE    multi tenancy: a new passphrase for a learner who lost theirs
#
# The API has to be running first (./scripts/dev.sh): this process owns no
# data and reaches everything through http://127.0.0.1:8765. Like the API it
# binds 127.0.0.1 only; the tunnel is what makes it reachable from a phone.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/apps/api/.venv"
PYTHON="$VENV/bin/python"

MCP_PORT="${VADEMECUM_MCP_PORT:-8766}"

say() { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m==> %s\033[0m\n' "$*" >&2; exit 1; }

USAGE="usage: $0 [--http|--stdio|--tunnel|setup codex|setup claude|passphrase|status|revoke-all|invite|learners|disable HANDLE|reset HANDLE]"

# A wrong argument is answered before anything else, virtualenv or not.
case "${1:-}" in
  "" | --http | --stdio | --tunnel | passphrase | status | revoke-all | invite | learners | disable | reset | setup) ;;
  *) echo "$USAGE" >&2; exit 64 ;;
esac

[ -x "$PYTHON" ] || die "No virtualenv yet. Run ./scripts/dev.sh --check first."

# The MCP package lives beside the API in the same virtualenv. An older venv
# predates it; install it the first time rather than failing on import.
if ! "$PYTHON" -c 'import vademecum_mcp' >/dev/null 2>&1; then
  say "Installing the MCP package into the virtualenv"
  "$PYTHON" -m pip install --quiet -e "$ROOT/apps/mcp"
fi

MODE="http"
case "${1:-}" in
  "" | --http) MODE="http" ;;
  --stdio) MODE="stdio" ;;
  --tunnel) MODE="tunnel" ;;
  passphrase | status | revoke-all | invite | learners)
    exec "$PYTHON" -m vademecum_mcp "$1"
    ;;
  disable | reset)
    exec "$PYTHON" -m vademecum_mcp "$1" "${2:-}"
    ;;
  setup)
    shift
    exec "$PYTHON" -m vademecum_mcp setup "$@"
    ;;
  *)
    echo "$USAGE" >&2
    exit 64
    ;;
esac

if [ "$MODE" = "stdio" ]; then
  # stdout is the protocol channel here; nothing else may write to it.
  exec "$PYTHON" -m vademecum_mcp serve --stdio
fi

if [ "$MODE" = "tunnel" ]; then
  command -v tailscale >/dev/null 2>&1 || die "tailscale is not on PATH. Install Tailscale, or run --http with your own tunnel."
  if [ -z "${VADEMECUM_MCP_PUBLIC_URL:-}" ]; then
    # The machine's MagicDNS name, which is what Funnel serves under https://.
    DNS_NAME="$(tailscale status --self --json 2>/dev/null \
      | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("Self") or {}).get("DNSName","").rstrip("."))')"
    [ -n "$DNS_NAME" ] || die "Could not read this Mac's Tailscale name. Is Tailscale signed in?"
    export VADEMECUM_MCP_PUBLIC_URL="https://$DNS_NAME"
  fi
fi

[ -n "${VADEMECUM_MCP_PUBLIC_URL:-}" ] || die "Set VADEMECUM_MCP_PUBLIC_URL to the HTTPS origin your tunnel presents, or use --tunnel."

SERVER_PID=""
FUNNEL_PID=""
cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
  # A foreground funnel is removed by Tailscale when its process ends, so
  # stopping it is all the cleanup the tunnel needs.
  [ -n "$FUNNEL_PID" ] && kill "$FUNNEL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

say "Starting the MCP server on http://127.0.0.1:$MCP_PORT (public: $VADEMECUM_MCP_PUBLIC_URL)"
"$PYTHON" -m vademecum_mcp serve --http &
SERVER_PID=$!

if [ "$MODE" = "tunnel" ]; then
  say "Opening a Tailscale Funnel to it. Ctrl-C closes both."
  # Foreground funnel: it is removed again when this script exits.
  tailscale funnel "$MCP_PORT" &
  FUNNEL_PID=$!
  while kill -0 "$SERVER_PID" 2>/dev/null && kill -0 "$FUNNEL_PID" 2>/dev/null; do
    sleep 1
  done
  die "The server or the funnel stopped. See the output above."
fi

say "Ctrl-C to stop."
wait "$SERVER_PID"
