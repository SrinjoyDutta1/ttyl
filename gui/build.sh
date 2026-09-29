#!/bin/bash
# Build the ttyl menu bar app with plain swiftc (no Xcode project).
#   ./build.sh            -> gui/build/ttyl.app
#   ./build.sh --install  -> also copy to ~/Applications and launch it
set -euo pipefail
cd "$(dirname "$0")"

APP=build/ttyl.app
TTYL="${TTYL:-$(command -v ttyl || true)}"  # baked in so the app finds the engine without your PATH
rm -rf "$APP" build/obj
mkdir -p "$APP/Contents/MacOS" build/obj

for arch in arm64 x86_64; do
  xcrun swiftc -O -swift-version 5 -parse-as-library -target "$arch-apple-macos14.0" \
    Sources/*.swift -o "build/obj/ttyl-bar-$arch"
done
lipo -create build/obj/ttyl-bar-* -output "$APP/Contents/MacOS/ttyl-bar"
sed "s|__TTYL__|$TTYL|" Info.plist > "$APP/Contents/Info.plist"
codesign --force --sign - "$APP" >/dev/null 2>&1
echo "built $APP (engine: ${TTYL:-ttyl on PATH})"

if [[ "${1:-}" == "--install" ]]; then
  mkdir -p ~/Applications
  pkill -x ttyl-bar 2>/dev/null || true
  rm -rf ~/Applications/ttyl.app
  cp -R "$APP" ~/Applications/
  open ~/Applications/ttyl.app
  echo "installed ~/Applications/ttyl.app (look for ☎ in your menu bar)"
fi
