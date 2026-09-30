#!/bin/bash
# Run install.sh the way a brand-new Mac would: an empty HOME and only the OS's own
# tools on PATH (Apple's python3 3.9, no pipx, no uv, no Homebrew). Nothing outside
# the temporary HOME is touched, and the menu bar app is built but not opened.
#   scripts/test_install.sh           test this checkout
#   scripts/test_install.sh --github  test what's published on GitHub
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
[[ "${1:-}" == "--github" ]] && REPO="git+https://github.com/SrinjoyDutta1/ttyl"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/ttyl-clean-XXXXXX")
mkdir -p "$TMP/home"
run() { env -i HOME="$TMP/home" PATH=/usr/bin:/bin:/usr/sbin:/sbin TERM=xterm-256color COLUMNS=110 "$@"; }

echo "== clean machine: $(run /usr/bin/python3 --version 2>&1), pipx: $(run bash -c 'command -v pipx || echo none'), uv: $(run bash -c 'command -v uv || echo none')"
run TTYL_REPO="$REPO" TTYL_APP=0 bash install.sh

TTYL="$TMP/home/.local/bin/ttyl"
echo "== the installed command"
run "$TTYL" ls --demo | head -4
run "$TTYL" summaries
echo "== the menu bar app builds from the installed package"
run "$TTYL" app --build-only
echo "== ok (everything is in $TMP)"
