"""
py2app build script for NetMonitor.

Build the app:
    python3 setup.py py2app

Output: dist/NUM.app
"""

from setuptools import setup

APP = ["run.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "NUM",
        "CFBundleDisplayName": "NUM",
        "CFBundleIdentifier": "com.charaka-codes.num",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "LSUIElement": True,  # menu bar only — no Dock icon
        "NSHumanReadableCopyright": "MIT License",
        # Required so macOS will show the Location prompt; without this string
        # the OS silently denies location and the Wi-Fi name stays hidden.
        "NSLocationUsageDescription":
            "NUM uses your location only to read the current Wi-Fi "
            "network name, so it can show data usage per network. Your "
            "location is never stored or shared.",
        "NSLocationWhenInUseUsageDescription":
            "NUM uses your location only to read the current Wi-Fi "
            "network name, so it can show data usage per network. Your "
            "location is never stored or shared.",
    },
    "packages": [],
    "excludes": ["tkinter", "Tkinter", "tcl", "tk", "_tkinter",
                 "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
                 "numpy", "scipy", "pandas", "matplotlib", "test"],
    "includes": ["netmonitor.core", "netmonitor.app", "netmonitor.dashboard",
                 "netmonitor.popover", "netmonitor.settings",
                 "netmonitor.report", "netmonitor.make_dashboard",
                 "WebKit", "AppKit", "Foundation", "CoreLocation", "ServiceManagement"],
    "iconfile": "assets/DUM.icns" if __import__("os").path.exists("assets/DUM.icns") else None,
}

# Bundle the menu-bar template images into Resources.
import os as _os
if _os.path.isdir("assets"):
    DATA_FILES = [("", ["assets/menubar_Template.png", "assets/menubar_Template@2x.png"])]

setup(
    app=APP,
    name="NUM",
    data_files=DATA_FILES,
    options={"py2app": {k: v for k, v in OPTIONS.items() if v is not None}},
    setup_requires=["py2app"],
)
