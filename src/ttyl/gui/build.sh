#!/bin/bash
# Build the ttyl menu bar app with plain swiftc (no Xcode project needed, just the
# Command Line Tools). Usually run for you by `ttyl app`.
#   build.sh             -> $OUT/ttyl.app   (OUT defaults to ~/Library/Caches/ttyl/build)
#   build.sh --install   -> also copy to ~/Applications and open it
# ARCHS="arm64 x86_64" builds a universal app; the default is this Mac's architecture.
set -euo pipefail
cd "$(dirname "$0")"

OUT="${OUT:-$HOME/Library/Caches/ttyl/build}"
ARCHS="${ARCHS:-$(uname -m)}"
APP="$OUT/ttyl.app"
TTYL="${TTYL:-$(command -v ttyl || true)}"  # baked in so the app finds the engine without your PATH
HASH=$(cat Sources/*.swift Info.plist | shasum | cut -c1-12)  # `ttyl app` rebuilds when this changes

rm -rf "$APP" "$OUT/obj"
mkdir -p "$APP/Contents/MacOS" "$OUT/obj"
for arch in $ARCHS; do
  xcrun swiftc -O -swift-version 5 -parse-as-library -target "$arch-apple-macos14.0" \
    Sources/*.swift -o "$OUT/obj/ttyl-bar-$arch"
done
lipo -create "$OUT"/obj/ttyl-bar-* -output "$APP/Contents/MacOS/ttyl-bar"
sed -e "s|__TTYL__|$TTYL|" -e "s|__HASH__|$HASH|" Info.plist > "$APP/Contents/Info.plist"
codesign --force --sign - "$APP" >/dev/null 2>&1
echo "built $APP ($ARCHS; engine: ${TTYL:-ttyl on PATH})"

# smoke check: lay the real panel out offscreen; a collapsed session list means a layout bug
if [[ -n "$TTYL" ]]; then
  size=$("$APP/Contents/MacOS/ttyl-bar" --snapshot-live "$OUT/live.png" | awk '{print $2}')
  height=${size#*x}
  if (( height < 200 )); then echo "panel layout looks collapsed (${size}); see $OUT/live.png" >&2; exit 1; fi
fi

if [[ "${1:-}" == "--install" ]]; then
  mkdir -p ~/Applications
  pkill -x ttyl-bar 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -x ttyl-bar >/dev/null || break; sleep 0.3; done  # let it quit
  rm -rf ~/Applications/ttyl.app
  cp -R "$APP" ~/Applications/
  # relaunching right after a quit can race LaunchServices (error -600): try again once
  open ~/Applications/ttyl.app 2>/dev/null || { sleep 1; open ~/Applications/ttyl.app; }
  echo "installed ~/Applications/ttyl.app: look for ☎ in your menu bar"
fi
