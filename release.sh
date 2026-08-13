#!/bin/bash
# release.sh — build the app AND package it into a drag-and-drop DMG,
# in one command.
set -e

echo "======================================"
echo "  Building NUM.app"
echo "======================================"
./build.sh

echo ""
echo "======================================"
echo "  Packaging DMG installer"
echo "======================================"
./make-dmg.sh

echo ""
echo "🎉 All done. Give NUM.dmg to anyone — they just"
echo "   open it and drag the app to Applications."
