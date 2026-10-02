#!/usr/bin/env bash
#
# Install Vademecum on this Mac and register it with the assistants you have.
#
#   ./scripts/install.sh
#
# What it does, in order, and says so as it goes:
#   1. finds Python 3.12 or newer (offers to install it with Homebrew if absent)
#   2. creates the virtualenv and installs the API and the MCP server into it
#   3. builds the web app if Node is present (otherwise the checkout's build is used)
#   4. asks where your source folder should be and lays it out
#   5. registers the launcher with Codex (the ChatGPT app) and Claude Desktop,
#      whichever are installed
#
# Afterwards the assistant starts Vademecum itself, and Vademecum starts the
# API beside it. Material goes in the source folder; records live in
# ~/Library/Application Support/Vademecum.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
VENV="$API_DIR/.venv"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m==> %s\033[0m\n' "$*" >&2; exit 1; }

find_python() {
  for candidate in python3.14 python3.13 python3.12 python3 /opt/homebrew/opt/python@3.13/bin/python3.13 /opt/homebrew/opt/python@3.12/bin/python3.12; do
    if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
        command -v "$candidate" 2>/dev/null || echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

# --- 1. Python ---------------------------------------------------------------

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  command -v brew >/dev/null 2>&1 || die "Python 3.12 or newer is needed and Homebrew is not installed. Install Python from python.org, then run this again."
  printf 'Python 3.12 or newer is needed. Install python@3.13 with Homebrew now? [y/N] '
  read -r answer
  case "$answer" in
    y | Y | yes | YES) brew install python@3.13 ;;
    *) die "Not installed. Install Python 3.12+ and run this again." ;;
  esac
  PYTHON="$(find_python || true)"
  [ -n "$PYTHON" ] || die "Python was installed but not found on PATH. Open a new terminal and run this again."
fi
say "Using $PYTHON"

# --- 2. the virtualenv -------------------------------------------------------

if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" --version >/dev/null 2>&1; then
  say "Virtualenv already present"
else
  rm -rf "$VENV"
  say "Creating the virtualenv"
  "$PYTHON" -m venv "$VENV"
fi
say "Installing Vademecum"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -e "$API_DIR"
"$VENV/bin/python" -m pip install --quiet -e "$ROOT/apps/mcp"

# --- 3. the desk -------------------------------------------------------------

if command -v npm >/dev/null 2>&1; then
  say "Building the web app"
  (cd "$WEB_DIR" && npm install --no-audit --no-fund --silent && npm run build --silent) || die "The web app did not build."
elif [ -f "$WEB_DIR/dist/index.html" ]; then
  say "Node is not installed; using the web app build in the checkout"
else
  say "Node is not installed and there is no web app build; the assistant works without it"
fi

# --- 4. the source folder ----------------------------------------------------

DEFAULT_FOLDER="$HOME/Documents/Vademecum"
printf 'Where should your Vademecum source folder live? [%s] ' "$DEFAULT_FOLDER"
read -r chosen
"$VENV/bin/python" -m vademecum_mcp setup folder "${chosen:-$DEFAULT_FOLDER}" || die "The source folder could not be set up."

# --- 5. the assistants -------------------------------------------------------

REGISTERED=0
if [ -d "/Applications/ChatGPT.app" ] || [ -f "$HOME/.codex/config.toml" ]; then
  "$VENV/bin/python" -m vademecum_mcp setup codex && REGISTERED=1
fi
if [ -d "/Applications/Claude.app" ] || [ -f "$HOME/Library/Application Support/Claude/claude_desktop_config.json" ]; then
  "$VENV/bin/python" -m vademecum_mcp setup claude && REGISTERED=1
fi
if [ "$REGISTERED" -eq 0 ]; then
  say "Neither the ChatGPT app (Codex) nor Claude Desktop was found. Install one, then run: ./scripts/mcp.sh setup codex   or   ./scripts/mcp.sh setup claude"
fi

# --- 6. always ready ----------------------------------------------------------

printf 'Start Vademecum at login, so the dashboard in your browser or Dock is always ready? [Y/n] '
read -r at_login
case "${at_login:-Y}" in
  [Yy]*) "$VENV/bin/python" -m vademecum_mcp setup login || say "Could not set Vademecum to start at login; the assistant still starts it when needed." ;;
  *) say "Skipped. Run ./scripts/mcp.sh setup login later to change your mind." ;;
esac

cat <<EOF

  Vademecum is installed.

  Your material:  the source folder above; one folder per pile under
                  piles/highconfidence, piles/mediumconfidence or piles/lowconfidence
  Your records:   ~/Library/Application Support/Vademecum
  In Codex:       restart the ChatGPT app, then ask "show me my Vademecum cover sheet"
  In Claude:      the same, once Claude Desktop is restarted
  In the Dock:    open http://127.0.0.1:8765 in Safari and choose File > Add to Dock

  Vademecum is educational. Never put patient identifiers in the folder or the chat.

EOF
