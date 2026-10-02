#!/usr/bin/env bash
#
# A feedback session, in one command: the hosted product, on this Mac.
#
#   ./scripts/pilot.sh            API (multi tenancy, host mode) + gateway + a public tunnel
#   ./scripts/pilot.sh invite     one more invite code for the running pilot
#   ./scripts/pilot.sh reload     restart the gateway after a code change, same address
#   ./scripts/pilot.sh reset HANDLE      a new passphrase for a learner who lost theirs
#   ./scripts/pilot.sh disable HANDLE    stop a learner signing in
#   ./scripts/pilot.sh learners          who has a workspace
#
# Uses its own data directory (~/Library/Application Support/Vademecum-pilot)
# so the owner's single-tenancy workspace is never touched. The tunnel is
# Tailscale Funnel when this node is allowed to use it, otherwise a Cloudflare
# quick tunnel, whose address changes each run. Set VADEMECUM_MCP_PUBLIC_URL to
# use a tunnel of your own instead. Ctrl-C stops everything.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
PYTHON="$API_DIR/.venv/bin/python"
LOGS="${TMPDIR:-/tmp}/vademecum-pilot"
mkdir -p "$LOGS"

export VADEMECUM_DATA_DIR="${VADEMECUM_DATA_DIR:-$HOME/Library/Application Support/Vademecum-pilot}"
export VADEMECUM_TENANCY=multi
export VADEMECUM_MODEL_PROVIDER=host
API_PORT="${VADEMECUM_PORT:-8765}"
MCP_PORT="${VADEMECUM_MCP_PORT:-8766}"
export VADEMECUM_PORT="$API_PORT" VADEMECUM_MCP_PORT="$MCP_PORT"

say() { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m==> %s\033[0m\n' "$*" >&2; exit 1; }

[ -x "$PYTHON" ] || die "No virtualenv yet. Run ./scripts/dev.sh --check first."
"$PYTHON" -c 'import vademecum_mcp' >/dev/null 2>&1 || "$PYTHON" -m pip install --quiet -e "$ROOT/apps/mcp"

case "${1:-}" in
  invite | learners | status)
    exec "$PYTHON" -m vademecum_mcp "$1"
    ;;
  reload)
    # After a code change: stop the gateway; the running pilot restarts it
    # under the same public address.
    pkill -f "vademecum_mcp serve --http" && echo "Gateway reloading; the address is unchanged." || echo "No gateway running."
    exit 0
    ;;
  reset | disable)
    # The learner's passphrase, or their access, for the running pilot:
    # same data directory, same store, no restart needed.
    exec "$PYTHON" -m vademecum_mcp "$1" "${2:-}"
    ;;
esac

[ -f "$WEB_DIR/dist/index.html" ] || die "The desk is not built. Run: (cd apps/web && npm run build)"

API_PID=""
MCP_PID=""
TUNNEL_PID=""
cleanup() {
  [ -n "$MCP_PID" ] && kill "$MCP_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait_for() {
  for attempt in $(seq 1 80); do
    if curl -fsS "$1" >/dev/null 2>&1; then return 0; fi
    sleep 0.25
  done
  return 1
}

# --- the API -----------------------------------------------------------------

say "Starting the API (multi tenancy, host mode) on http://127.0.0.1:$API_PORT"
(cd "$API_DIR" && exec "$PYTHON" -m vademecum) >"$LOGS/api.log" 2>&1 &
API_PID=$!
wait_for "http://127.0.0.1:$API_PORT/api/health" || die "The API did not answer. See $LOGS/api.log"

# --- the tunnel --------------------------------------------------------------

if [ -z "${VADEMECUM_MCP_PUBLIC_URL:-}" ]; then
  FUNNEL="no"
  if command -v tailscale >/dev/null 2>&1; then
    FUNNEL="$(tailscale status --self --json 2>/dev/null | "$PYTHON" -c 'import json,sys
d=json.load(sys.stdin); caps=(d.get("Self") or {}).get("CapMap") or {}
print("yes" if "funnel" in caps else "no")' 2>/dev/null || echo no)"
  fi
  if [ "$FUNNEL" = "yes" ]; then
    DNS_NAME="$(tailscale status --self --json | "$PYTHON" -c 'import json,sys; print((json.load(sys.stdin).get("Self") or {}).get("DNSName","").rstrip("."))')"
    export VADEMECUM_MCP_PUBLIC_URL="https://$DNS_NAME"
    say "Opening a Tailscale Funnel at $VADEMECUM_MCP_PUBLIC_URL"
    tailscale funnel "$MCP_PORT" >"$LOGS/tunnel.log" 2>&1 &
    TUNNEL_PID=$!
  elif command -v cloudflared >/dev/null 2>&1; then
    say "Opening a Cloudflare quick tunnel (its address changes each run)"
    : >"$LOGS/tunnel.log"
    cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$MCP_PORT" >"$LOGS/tunnel.log" 2>&1 &
    TUNNEL_PID=$!
    PUBLIC=""
    for attempt in $(seq 1 120); do
      PUBLIC="$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOGS/tunnel.log" | head -1 || true)"
      [ -n "$PUBLIC" ] && break
      kill -0 "$TUNNEL_PID" 2>/dev/null || die "cloudflared stopped. See $LOGS/tunnel.log"
      sleep 0.5
    done
    [ -n "$PUBLIC" ] || die "No tunnel address appeared within a minute. See $LOGS/tunnel.log"
    export VADEMECUM_MCP_PUBLIC_URL="$PUBLIC"
  else
    die "No tunnel available: enable Tailscale Funnel, install cloudflared, or set VADEMECUM_MCP_PUBLIC_URL."
  fi
fi

# --- the gateway -------------------------------------------------------------

say "Starting the gateway on http://127.0.0.1:$MCP_PORT (public: $VADEMECUM_MCP_PUBLIC_URL)"
"$PYTHON" -m vademecum_mcp serve --http >"$LOGS/mcp.log" 2>&1 &
MCP_PID=$!
wait_for "http://127.0.0.1:$MCP_PORT/health" || die "The gateway did not answer. See $LOGS/mcp.log"

# Through the tunnel too, so a wrong Host allowlist shows up here and not in ChatGPT.
if wait_for "$VADEMECUM_MCP_PUBLIC_URL/health"; then
  say "The public address answers."
else
  say "The public address is not answering yet; a quick tunnel can take a moment."
fi

CODE="$("$PYTHON" -m vademecum_mcp invite | sed -n 's/.*shown once): //p')"

cat <<EOF

  Vademecum pilot is running.

  Desk (sign in, upload):   $VADEMECUM_MCP_PUBLIC_URL/login
  ChatGPT app endpoint:     $VADEMECUM_MCP_PUBLIC_URL/mcp
  Your invite code:         $CODE
  Data directory:           $VADEMECUM_DATA_DIR
  Logs:                     $LOGS

  In ChatGPT: Settings -> Apps & Connectors -> Advanced -> Developer mode -> Create,
  paste the endpoint, choose OAuth, and on the page that opens use "First time here?"
  with the invite code. The same handle and passphrase then sign you in to the desk.

  Another invite:           ./scripts/pilot.sh invite
  Ctrl-C stops everything.

EOF

# The gateway is restarted in place if it stops (`./scripts/pilot.sh reload`
# stops it on purpose after a code change), so the tunnel and its address
# survive. The API and the tunnel stopping end the session.
while kill -0 "$API_PID" 2>/dev/null; do
  if [ -n "$TUNNEL_PID" ] && ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    die "The tunnel stopped. See $LOGS/tunnel.log"
  fi
  if ! kill -0 "$MCP_PID" 2>/dev/null; then
    say "The gateway stopped; starting it again with the same address."
    "$PYTHON" -m vademecum_mcp serve --http >>"$LOGS/mcp.log" 2>&1 &
    MCP_PID=$!
    wait_for "http://127.0.0.1:$MCP_PORT/health" || die "The gateway did not come back. See $LOGS/mcp.log"
  fi
  sleep 1
done
die "The API stopped. See $LOGS/api.log"
