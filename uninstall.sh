#!/bin/bash
# uninstall.sh — properly remove NUM (v1, v2, or both) and, optionally, its
# usage data.
#
# This does three things the old version of this script did NOT do, each of
# which caused a real, confusing problem during testing:
#   1. Looked for "NetMonitor.app" — the app has never been called that; it's
#      "NUM.app" (v1) or "NUM V2.app" (v2). The old script deleted nothing,
#      ever, silently (rm -rf on a path that doesn't exist just succeeds).
#   2. Never quit a running instance before deleting its files — the already-
#      running process just keeps going regardless, with nothing left on
#      disk to quit from normally.
#   3. Looked for a LaunchAgent .plist to clean up "launch at login" — but
#      this app uses the modern SMAppService API (macOS 13+), which was
#      never a file on disk at all. SMAppService is bundle-identity-bound,
#      so it can only be unregistered correctly by the app's OWN code
#      running from its OWN bundle — an external script can't fake that.
#      This script now briefly launches the real binary with a quiet
#      cleanup flag (NUM_UNINSTALL_CLEANUP=1) so THAT code path does the
#      unregister correctly, before any files are removed.

set -e

found_any=false

cleanup_one () {
    local APP_PATH="$1"        # e.g. /Applications/NUM V2.app
    local DISPLAY_NAME="$2"    # e.g. "NUM V2"
    local DATA_DIR="$3"        # e.g. ~/.netmonitor-v2

    if [ ! -d "$APP_PATH" ]; then
        return
    fi
    found_any=true
    echo ""
    echo "=== $DISPLAY_NAME ==="

    echo "==> Quitting $DISPLAY_NAME if it's running…"
    osascript -e "tell application \"$DISPLAY_NAME\" to quit" >/dev/null 2>&1 || true
    sleep 1
    pkill -f "$APP_PATH/Contents/MacOS/" 2>/dev/null || true

    BIN="$APP_PATH/Contents/MacOS/$DISPLAY_NAME"
    if [ -x "$BIN" ]; then
        echo "==> Unregistering \"Launch at login\" (if it was on)…"
        NUM_UNINSTALL_CLEANUP=1 "$BIN" >/dev/null 2>&1 || true
    fi

    echo "==> Removing $APP_PATH…"
    rm -rf "$APP_PATH"

    if [ -d "$DATA_DIR" ]; then
        read -p "Also delete usage data in $DATA_DIR? [y/N] " ans
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            rm -rf "$DATA_DIR"
            echo "Usage data deleted."
        else
            echo "Usage data kept at $DATA_DIR"
        fi
    fi
}

cleanup_one "/Applications/NUM.app"    "NUM"    "$HOME/.netmonitor"
cleanup_one "/Applications/NUM V2.app" "NUM V2" "$HOME/.netmonitor-v2"

if [ "$found_any" = false ]; then
    echo "Neither NUM.app nor \"NUM V2.app\" was found in /Applications — nothing to remove."
fi

echo ""
echo "Note: macOS also keeps its own separate record of any app that has ever"
echo "added a menu bar item or login item (Background Task Management), so it"
echo "can still be audited after the app is deleted — that's by OS design, not"
echo "something this script (or any app) can clear from the outside. If a"
echo "stale entry still shows in System Settings → General → Login Items &"
echo "Extensions or → Menu Bar after this, it should disappear on its own"
echo "after a reboot."
echo ""
echo "Done."
