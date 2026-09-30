#!/bin/bash
# make-dmg.sh — build a drag-and-drop DMG installer for NUM.
#
# Produces NUM.dmg with the app and an Applications shortcut,
# so the user just drags NetMonitor onto Applications to install.
#
# Run ./build.sh first (this needs dist/NUM.app to exist).

set -e

APP=$(ls -d dist/*.app 2>/dev/null | head -n 1)
STAGING="dmg-staging"

if [ -z "$APP" ] || [ ! -d "$APP" ]; then
    echo "❌ No .app bundle found in dist/. Run ./build.sh first."
    exit 1
fi

# Name the DMG after whatever the app actually got built as (e.g. "NUM V2"),
# rather than assuming "NUM" — build.sh's output name depends on the build
# profile (NUM_PROFILE), so a hardcoded name here silently missed v2 builds.
APP_BASENAME=$(basename "$APP" .app)
DMG="${APP_BASENAME}.dmg"
VOLNAME="$APP_BASENAME"

echo "==> Found $APP"

echo "==> Preparing staging folder"
rm -rf "$STAGING" "$DMG"
mkdir -p "$STAGING"

# Copy the app in
cp -R "$APP" "$STAGING/"

# Create the Applications shortcut so users can drag onto it
ln -s /Applications "$STAGING/Applications"

echo "==> Building DMG"
hdiutil create \
    -volname "$VOLNAME" \
    -srcfolder "$STAGING" \
    -ov \
    -format UDZO \
    "$DMG" >/dev/null

echo "==> Cleaning up"
rm -rf "$STAGING"

echo ""
echo "✅ Done.  Installer is at: $DMG"
echo "   Share this file. Users open it and drag NUM to Applications."
