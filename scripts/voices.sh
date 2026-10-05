#!/usr/bin/env bash
# Natural podcast voices on this Mac (ADR 0027): Kokoro, an open speech model
# (Apache 2.0) that runs on-device. No account, no key; nothing is sent.
#
# One-time setup: installs the package into Vademecum's environment and
# downloads the model (about 325 MB) and the voice pack (about 28 MB) from the
# project's GitHub release, checked against their SHA-256 digests, into the
# data directory's voices/ folder. Restart Vademecum afterwards.
#
#   ./scripts/voices.sh            install (safe to run again)
#   ./scripts/voices.sh remove     delete the files; the Mac's own voices remain
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/apps/api/.venv/bin/python"
DATA="${VADEMECUM_DATA_DIR:-$HOME/Library/Application Support/Vademecum}"
VOICES="$DATA/voices"
RELEASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_SHA="7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"
PACK_SHA="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"

if [ "${1:-}" = "remove" ]; then
  rm -rf "$VOICES"
  echo "Removed $VOICES. Vademecum uses the Mac's own voices again after a restart."
  exit 0
fi

[ -x "$PYTHON" ] || { echo "Vademecum's environment is missing at $PYTHON" >&2; exit 1; }
echo "Installing the Kokoro package into Vademecum's environment…"
"$PYTHON" -m pip install --quiet -e "$ROOT/apps/api[voices]"

mkdir -p "$VOICES"
fetch() {
  local name="$1" sha="$2" target="$VOICES/$1"
  if [ -f "$target" ] && [ "$(shasum -a 256 "$target" | cut -d' ' -f1)" = "$sha" ]; then
    echo "$name: already here"
    return
  fi
  echo "Downloading $name…"
  curl -fL --progress-bar -o "$target.partial" "$RELEASE/$name"
  if [ "$(shasum -a 256 "$target.partial" | cut -d' ' -f1)" != "$sha" ]; then
    rm -f "$target.partial"
    echo "$name did not match its published digest; nothing was installed." >&2
    exit 1
  fi
  mv "$target.partial" "$target"
}
fetch kokoro-v1.0.onnx "$MODEL_SHA"
fetch voices-v1.0.bin "$PACK_SHA"

# espeak keeps its data path in a short buffer: a copy at this short path works
# where the package's own, deep in the environment, may not.
DATA_SRC="$("$PYTHON" -c 'import espeakng_loader; print(espeakng_loader.get_data_path())')"
rm -rf "$VOICES/espeak-ng-data"
cp -R "$DATA_SRC" "$VOICES/espeak-ng-data"

"$PYTHON" - "$VOICES" <<'PY'
import sys, tempfile
from pathlib import Path
from vademecum.model import kokoro
directory = Path(sys.argv[1])
assert kokoro.ready(directory), "the files are not all in place"
with tempfile.TemporaryDirectory() as scratch:
    kokoro.synth(directory, "Vademecum's voices are ready.", "kokoro:af_heart", Path(scratch) / "check.wav")
print("Kokoro works on this Mac.")
PY
echo "Done. Restart Vademecum: ./scripts/mcp.sh setup login --model codex (or your usual start)."
