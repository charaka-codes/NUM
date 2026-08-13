#!/bin/bash
# build.sh — build NUM.app from source.
set -e

echo "==> Creating build virtualenv"
python3 -m venv .buildenv
source .buildenv/bin/activate

echo "==> Installing dependencies"
pip install --upgrade pip wheel >/dev/null
pip install pyobjc-framework-Cocoa pyobjc-framework-WebKit pyobjc-framework-CoreLocation pyobjc-framework-CoreWLAN pyobjc-framework-ServiceManagement py2app >/dev/null

echo "==> Cleaning previous builds"
rm -rf build dist

echo "==> Building NUM.app"
python setup.py py2app

deactivate
echo ""
echo "✅ Done.  App is at: dist/NUM.app"
echo "   Drag it into /Applications to install."
