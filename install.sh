#!/bin/bash
# Install ttyl:  curl -fsSL https://raw.githubusercontent.com/SrinjoyDutta1/ttyl/main/install.sh | bash
#
# Installs the `ttyl` command in its own environment (pipx if you have it and a
# Python 3.11+, otherwise uv, which brings its own Python), then on a Mac builds
# and opens the menu bar app.
#   TTYL_APP=0   skip building the menu bar app       TTYL_REPO=<path|url>   install from elsewhere
set -euo pipefail
REPO="${TTYL_REPO:-git+https://github.com/SrinjoyDutta1/ttyl}"

have() { command -v "$1" >/dev/null 2>&1; }
modern_python() {
  for py in python3.14 python3.13 python3.12 python3.11 python3; do
    if have "$py" && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      command -v "$py"; return 0
    fi
  done
  return 1
}

if have pipx && PY=$(modern_python); then
  echo "→ installing ttyl with pipx ($PY)"
  pipx install --force --python "$PY" "$REPO"
else
  if ! have uv; then
    echo "→ installing uv (a Python tool installer that brings its own Python)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
  echo "→ installing ttyl with uv"
  uv tool install --force --python 3.12 "$REPO"
fi

TTYL="$(command -v ttyl || echo "$HOME/.local/bin/ttyl")"
if [[ "$(uname)" == "Darwin" && "${TTYL_APP:-1}" != "0" ]]; then
  echo "→ building the menu bar app"
  "$TTYL" app || echo "(skipped the menu bar app; run \`ttyl app\` later)"
fi

cat <<'MSG'

✓ ttyl is installed.
    ttyl         the terminal view
    ttyl app     the menu bar app (look for ☎ at the top of your screen)
    ttyl --demo  try it on made-up sessions
If `ttyl` isn't found, open a new terminal (or add ~/.local/bin to your PATH).
MSG
