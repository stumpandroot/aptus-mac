#!/bin/bash
# Signs dist/Aptus.app, wraps it in a DMG, and (when Apple details are set) notarizes it.
#
#   MACOS_SIGN_IDENTITY   "Developer ID Application: Your Name (TEAMID)". Unset = unsigned test build.
#   APPLE_ID, APPLE_APP_PASSWORD, APPLE_TEAM_ID   for notarizing. Unset = signed but not notarized.
#   DMG_NAME              file name for the DMG, for example Aptus-1.0.0-apple-silicon.dmg
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/dist/Aptus.app"
DMG="$ROOT/dist/${DMG_NAME:-Aptus.dmg}"
ENT="$ROOT/mac/entitlements.plist"
IDENTITY="${MACOS_SIGN_IDENTITY:-}"

[ -d "$APP" ] || { echo "Build the app first: pyinstaller --noconfirm mac/Aptus.spec" >&2; exit 1; }

if [ -z "$IDENTITY" ]; then
  echo "No signing identity set. Making an unsigned test build (ad-hoc signature)."
  codesign --force --deep --sign - "$APP"
else
  echo "Signing with $IDENTITY"
  # Sign the inside first: every library and helper tool, then the app itself.
  find "$APP/Contents" -type f \( -name "*.so" -o -name "*.dylib" -o -name "ffmpeg" -o -name "ffprobe" -o -perm -111 \) \
    ! -path "*/Contents/MacOS/Aptus" -print0 |
    while IFS= read -r -d '' f; do
      if file "$f" | grep -q "Mach-O"; then
        codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$IDENTITY" "$f"
      fi
    done
  codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
fi

echo "Making $DMG"
rm -f "$DMG"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Aptus" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

if [ -n "$IDENTITY" ]; then
  codesign --force --timestamp --sign "$IDENTITY" "$DMG"
  if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
    echo "Notarizing (this usually takes a few minutes)"
    xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --password "$APPLE_APP_PASSWORD" \
      --team-id "$APPLE_TEAM_ID" --wait
    xcrun stapler staple "$DMG"
    spctl --assess --type open --context context:primary-signature --verbose "$DMG"
  else
    echo "Apple ID details not set, so the DMG is signed but not notarized."
  fi
fi
echo "Done: $DMG"
