"""
updates.py — manual "check for updates" for NUM.

Privacy note: this module makes NUM's ONLY outbound network request, and ONLY
when the user explicitly clicks "Check for updates". There is no automatic or
background checking. Nothing about the user is sent — it's a plain GET of a
small public JSON file.

How it works:
  * You host a tiny version.json on GitHub (see VERSION_URL) that looks like:
        {"version": "3.0.0",
         "url": "https://github.com/charaka-codes/NUM/releases/latest",
         "notes": "Adds per-app monitoring and a privacy toggle."}
  * The app fetches it, compares the advertised version to its own, and returns
    a small result the UI shows. That's it — no download, no install.
"""

import json
import urllib.request
from urllib.error import URLError, HTTPError

# Where the latest-version manifest lives. Point this at a raw file in your repo
# so you can update it whenever you cut a release, without changing the app.
VERSION_URL = (
    "https://raw.githubusercontent.com/charaka-codes/NUM/main/version.json"
)

# Where to send the user to get the new version.
DEFAULT_DOWNLOAD_URL = "https://github.com/charaka-codes/NUM/releases/latest"


def _parse_version(v):
    """Turn '2.10.1' into a comparable tuple (2, 10, 1). Non-numeric parts are
    ignored gracefully so a malformed string never crashes the check."""
    parts = []
    for chunk in str(v).strip().split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    # pad to 3 for stable comparison
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(latest, current):
    """True if `latest` is a newer version than `current`."""
    try:
        return _parse_version(latest) > _parse_version(current)
    except Exception:
        return False


def check_for_updates(current_version, timeout=8):
    """
    Fetch the version manifest and compare. Returns a dict the UI can show:

        {"status": "update",     "latest": "3.0.0", "url": ..., "notes": ...}
        {"status": "current",    "latest": "2.0.0"}
        {"status": "error",      "message": "Couldn't reach the update server."}

    Never raises — always returns a dict, so the UI can show something sensible.
    This is the only place NUM makes a network request, and only when called
    from the user's explicit "Check for updates" click.
    """
    try:
        req = urllib.request.Request(
            VERSION_URL, headers={"User-Agent": "NUM-update-check"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError):
        return {"status": "error",
                "message": "Couldn't reach the update server. "
                           "Check your connection and try again."}
    except Exception:
        return {"status": "error",
                "message": "Couldn't read update information."}

    latest = str(data.get("version", "")).strip()
    if not latest:
        return {"status": "error",
                "message": "Update information was incomplete."}

    url = data.get("url") or DEFAULT_DOWNLOAD_URL
    notes = data.get("notes", "")

    if is_newer(latest, current_version):
        return {"status": "update", "latest": latest, "url": url,
                "notes": notes}
    return {"status": "current", "latest": latest}
