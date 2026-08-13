#!/bin/bash
# make-dmg.sh — build a drag-and-drop DMG installer for NUM.
#
# Produces NUM.dmg with the app and an Applications shortcut,
# so the user just drags NetMonitor onto Applications to install.
#
# Run ./build.sh first (this needs dist/NUM.app to exist).

set -e

APP="dist/NUM.app"
DMG="NUM.dmg"
VOLNAME="NUM"
STAGING="dmg-staging"

if [ ! -d "$APP" ]; then
    echo "❌ $APP not found. Run ./build.sh first."
    exit 1
fi

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
