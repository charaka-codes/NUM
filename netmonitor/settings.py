"""
settings.py — persistent user preferences stored as JSON in ~/.netmonitor.

Keys and defaults:
  tracking        bool   True   — whether the sampler records traffic
  show_menu_text  bool   True   — show the data total next to the menu bar icon
  transparency    int    60     — panel translucency 0..100 (higher = more solid)
  down_color      str    "#2e9e5b"  — download accent color
  up_color        str    "#e0603f"  — upload accent color
"""

import json
from pathlib import Path

SETTINGS_PATH = Path.home() / ".netmonitor" / "settings.json"

DEFAULTS = {
    "tracking": True,
    "show_menu_text": True,
    "launch_at_login": False,
    "transparency": 60,
    "down_color": "#2e9e5b",
    "up_color": "#e0603f",
}


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
                        data[k] = saved[k]
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
