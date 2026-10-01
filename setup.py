"""
py2app build script for NUM.

Build the app:
    python3 setup.py py2app                 # builds NUM.app (v1)
    NUM_PROFILE=v2 python3 setup.py py2app   # builds "NUM 2.app" (v2, per-app)

The profile switch lets v1 and v2 install side by side as SEPARATE apps —
distinct names, bundle IDs, and (at runtime, via core.py) data folders — so
building/testing v2 never disturbs an installed v1. The NUM_PROFILE value is
baked into the bundle so the built app uses the right data folder even though
it's launched by double-click (no env var at launch time).

Output: dist/NUM.app  (or dist/NUM 2.app for v2)
"""

import os
from setuptools import setup

# ---- profile-aware identity ------------------------------------------------

PROFILE = os.environ.get("NUM_PROFILE", "").strip()

if PROFILE:
    APP_NAME = f"NUM {PROFILE.upper()}"          # e.g. "NUM V2"
    BUNDLE_ID = f"com.charaka-codes.num{PROFILE}"  # e.g. com.charaka-codes.numv2
else:
    APP_NAME = "NUM"
    BUNDLE_ID = "com.charaka-codes.num"

APP = ["run.py"]
DATA_FILES = []

# Bake the profile into the bundle so the launched app knows which data folder
# to use (core.py reads NUM_PROFILE; we set it via LSEnvironment in the plist).
_plist = {
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": APP_NAME,
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleVersion": "2.5.1" if PROFILE else "1.0.0",
    "CFBundleShortVersionString": "2.5.1" if PROFILE else "1.0.0",
    "LSUIElement": True,  # menu bar only — no Dock icon
    "NSHumanReadableCopyright": "MIT License",
    # Required so macOS will show the Location prompt; without this string the
    # OS silently denies location and the Wi-Fi name stays hidden.
    "NSLocationUsageDescription":
        "NUM uses your location only to read the current Wi-Fi network name, "
        "so it can show data usage per network. Your location is never stored "
        "or shared.",
    "NSLocationWhenInUseUsageDescription":
        "NUM uses your location only to read the current Wi-Fi network name, "
        "so it can show data usage per network. Your location is never stored "
        "or shared.",
}

# Bake the profile into the app's environment so the double-clicked bundle uses
# the correct data folder without needing an env var at launch.
if PROFILE:
    _plist["LSEnvironment"] = {"NUM_PROFILE": PROFILE}

OPTIONS = {
    "argv_emulation": False,
    "plist": _plist,
    "packages": [],
    "excludes": ["tkinter", "Tkinter", "tcl", "tk", "_tkinter",
                 "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
                 "numpy", "scipy", "pandas", "matplotlib", "test"],
    "includes": ["netmonitor.core", "netmonitor.app", "netmonitor.dashboard",
                 "netmonitor.popover", "netmonitor.settings",
                 "netmonitor.report", "netmonitor.make_dashboard",
                 "netmonitor.apps",  # <-- v2 per-app module (harmless in v1)
                 "netmonitor.updates",  # <-- manual update check
                 "WebKit", "AppKit", "Foundation", "CoreLocation",
                 "ServiceManagement"],
    "iconfile": "assets/DUM.icns" if os.path.exists("assets/DUM.icns") else None,
}

# Bundle the menu-bar template images into Resources.
# NOTE: filenames must match what's actually in assets/. app.py loads
# "menubar_Template.png"; the retina file is "menubar_Template_2x.png".
if os.path.isdir("assets"):
    _imgs = []
    for fn in ("menubar_Template.png", "menubar_Template_2x.png",
               "menubar_Template@2x.png"):
        p = os.path.join("assets", fn)
        if os.path.exists(p):
            _imgs.append(p)
    if _imgs:
        DATA_FILES = [("", _imgs)]

setup(
    app=APP,
    name=APP_NAME,
    data_files=DATA_FILES,
    options={"py2app": {k: v for k, v in OPTIONS.items() if v is not None}},
    setup_requires=["py2app"],
)
