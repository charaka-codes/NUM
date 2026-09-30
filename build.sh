#!/bin/bash
# build.sh — build NUM.app from source.
set -e

# v2 is the real, shipped app now (v1 was only for running the two side by
# side during the rebuild) — default here too, same reasoning as core.py's
# DATA_DIR default.
#
# Note: setup.py's TRUE original v1 identity (name "NUM", bundle id
# com.charaka-codes.num) only exists when NUM_PROFILE is truly empty —
# NUM_PROFILE=v1 explicitly builds a DIFFERENT bundle ("NUM V1"), not that
# one. Bash's ${VAR:=default} can't distinguish "empty" from "unset", so
# this default forecloses building that exact original bundle again. Given
# v1 is retired, that's an acceptable tradeoff — but if it's ever needed,
# comment out the next two lines rather than trying to override with an
# empty value, which won't work.
: "${NUM_PROFILE:=v2}"
export NUM_PROFILE
echo "==> Building with NUM_PROFILE=$NUM_PROFILE"

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

BUILT_APP=$(ls -d dist/*.app 2>/dev/null | head -n 1)
N_APPS=$(ls -d dist/*.app 2>/dev/null | wc -l | tr -d ' ')
echo ""
if [ -z "$BUILT_APP" ]; then
    echo "❌ Build finished but no .app bundle was found in dist/ — something"
    echo "   went wrong. Check the py2app output above for errors."
    exit 1
fi
if [ "$N_APPS" -gt 1 ]; then
    echo "⚠️  More than one .app found in dist/ — reporting the first:"
fi
echo "✅ Done.  App is at: $BUILT_APP"
echo "   Drag it into /Applications to install."
