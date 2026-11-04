#!/bin/bash
# Build the Ambilight Hub Mac app with the Command Line Tools (no Xcode needed).
#   ./build.sh           build build/Ambilight Hub.app
#   ./build.sh test      run the app's logic tests
#   ./build.sh install   build, then copy the app to /Applications
set -euo pipefail
cd "$(dirname "$0")"
TARGET=arm64-apple-macos14.0
FLAGS=(-O -swift-version 5 -target "$TARGET")
mkdir -p build

if [[ "${1:-}" == "test" ]]; then
  swiftc "${FLAGS[@]}" Sources/Layout.swift Tests/main.swift -o build/layout-tests
  exec build/layout-tests
fi

APP="build/Ambilight Hub.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc "${FLAGS[@]}" -parse-as-library Sources/*.swift -o "$APP/Contents/MacOS/AmbilightHub"
cp Info.plist "$APP/Contents/Info.plist"

ICONSET=build/AppIcon.iconset
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
swift Icon.swift build/icon-1024.png
for size in 16 32 128 256 512; do
  sips -z $size $size build/icon-1024.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) build/icon-1024.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

# Signed for this Mac only; another Mac would need a Developer ID and notarisation.
codesign --force --sign - --timestamp=none "$APP"
codesign --verify --strict "$APP"
echo "Built $APP"

if [[ "${1:-}" == "install" ]]; then
  rm -rf "/Applications/Ambilight Hub.app"
  cp -R "$APP" /Applications/
  echo "Installed /Applications/Ambilight Hub.app"
fi
