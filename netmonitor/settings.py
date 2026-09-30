"""
settings.py — persistent user preferences stored as JSON in ~/.netmonitor.

Keys and defaults:
  tracking          bool   True   — whether the sampler records traffic
  show_menu_text    bool   True   — show the data total next to the menu bar icon
  transparency      int    15     — panel translucency 0..100 (higher = more solid).
                                     User-adjustable via the Settings slider. The
                                     underlying floor was widened 2026-09-23 so 0 is
                                     genuinely more transparent than the old fixed
                                     range allowed, not just the same floor with a
                                     slider stuck on top of it.
  down_color_light  str    "#248a3d"  — download accent color, light theme (deep green)
  up_color_light    str    "#e0342a"  — upload accent color, light theme (deep red)
  down_color_dark   str    "#34c759"  — download accent color, dark theme (bright green)
  up_color_dark     str    "#ffd60a"  — upload accent color, dark theme (bright yellow)
"""

import json
from pathlib import Path

# Derive the settings path from core's profile-aware DATA_DIR so v1 and v2 keep
# separate preferences. Falls back to the v1 location if core can't be imported
# for any reason (keeps this module standalone-safe).
try:
    from .core import DATA_DIR as _DATA_DIR
except Exception:
    try:
        from core import DATA_DIR as _DATA_DIR
    except Exception:
        _DATA_DIR = Path.home() / ".netmonitor-v2"

SETTINGS_PATH = _DATA_DIR / "settings.json"

DEFAULTS = {
    "tracking": True,
    "show_menu_text": True,
    # Which figure the menu bar shows when show_menu_text is on:
    # "down", "up", or "total" (down+up). Total suits data-cap watching.
    "menu_number": "total",
    "launch_at_login": False,
    # User-adjustable via the Settings slider. 15 as a shipped default keeps
    # new installs looking properly frosted/glassy out of the box without
    # forcing the extreme low end, which people clearly do want to reach
    # (real usage during testing settled at 0) but isn't necessarily what
    # a first-run default should look like.
    "transparency": 15,
    # Separate colour pairs per theme — picking a colour that reads well on
    # a light card can be unreadable on dark and vice versa, so light and
    # dark each get their own pair rather than sharing one. Bright/saturated
    # colours pop against the dark vibrancy blur; deeper/muted colours stay
    # legible against the light one — light and dark are NOT the same
    # colours at different brightness, they're deliberately opposite ends.
    # Defaults below are the shipped palette; "Reset to default" in Settings
    # restores exactly these four values.
    "down_color_light": "#248a3d",
    "up_color_light": "#e0342a",
    "down_color_dark": "#34c759",
    "up_color_dark": "#ffd60a",
    # Per-app monitoring (v2). On by default in this build so the feature is
    # visible immediately for testing; make it opt-in for a public release.
    "app_tracking": True,
    "app_sample_interval": 30,
    # How often the long-lived nettop REPORTS (seconds). Accuracy does not
    # depend on this — the reader is always running, so every byte is counted
    # either way; it only sets how often we are told. 5s keeps the Apps tab
    # responsive; accuracy does not depend on it.
    "app_reader_interval": 10,
    # Whether to read & show Wi-Fi network NAMES (SSIDs). This needs Location
    # permission on macOS 26. Off → the app never requests Location and labels
    # usage generically ("Wi-Fi"), which is the privacy-respecting choice.
    # Show system / local-network services (Bonjour, AirDrop, Continuity) in
    # the Apps tab. They are always RECORDED and never counted in the total —
    # this only controls whether their group is displayed.
    "show_system_apps": False,
    "network_names": False,
    # Last measured panel height (px). Persisted only so the panel opens at the
    # right size instead of visibly resizing; the panel is still fully dynamic
    # and re-measures every open. Not user-facing.
    "panel_height": 0,
    # Network identity: maps a stable fingerprint (gateway MAC) to info:
    #   { "gw:50:2b:..": {"name": "Home", "auto": "Network 1", "ssid": "..."} }
    # "name" is the user's custom name (wins). "auto" is the auto-assigned
    # "Network N" (stable per fingerprint). "ssid" is the last-seen real name,
    # shown ONLY if Location permission is on. Nothing here is displayed without
    # either a custom name, an auto name, or granted permission.
    "networks": {},
    # Counter for assigning the next "Network N" auto name.
    "network_counter": 0,
    # "system" follows the OS's light/dark setting live; "light"/"dark" force
    # it regardless. Applied both natively (the popover's own NSAppearance,
    # which the adaptive vibrancy material renders light/dark from) and in
    # the page's CSS (prefers-color-scheme + a data-theme override) — see
    # popover.py's set_theme() and dashboard.py's applyTheme().
    "theme": "system",
    # Cache for the All-time tab's Generate button: {down, up, first_day,
    # months, generated_at}. Computed on demand (not on the live refresh
    # tick — see core.all_time_total()) and persisted so the number survives
    # a relaunch instead of needing to be regenerated every time. Empty dict
    # means "never generated yet".
    "alltime_cache": {},
}


# Keys whose value must always be a real bool. A past bug (show_system_apps
# missing from app.py's boolean-conversion list) wrote "0"/"1" as literal
# strings for one release; a downstream `=== false` check in the panel's JS
# never matches a string, so a saved "off" silently read back as "on". Coerce
# on load so any already-corrupted settings.json self-heals without the user
# needing to re-toggle it.
_BOOL_KEYS = {
    "tracking", "show_menu_text", "launch_at_login", "app_tracking",
    "show_system_apps", "network_names",
}


def _coerce_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def load():
    """Return the settings dict, filling any missing keys with defaults."""
    data = dict(DEFAULTS)
    try:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for k in DEFAULTS:
                    if k in saved:
                        v = saved[k]
                        data[k] = _coerce_bool(v) if k in _BOOL_KEYS else v
    except Exception:
        pass
    return data


def save(data):
    """Persist the settings dict (only known keys)."""
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        clean = {k: data.get(k, DEFAULTS[k]) for k in DEFAULTS}
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2)
    except Exception:
        pass


def set_value(key, value):
    """Update a single setting and persist."""
    data = load()
    if key in DEFAULTS:
        data[key] = value
        save(data)
    return data
