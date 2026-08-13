#!/bin/bash
# uninstall.sh — remove NetMonitor and (optionally) its data.

echo "Removing NetMonitor.app from /Applications…"
rm -rf /Applications/NetMonitor.app

# Remove login-item launch agent if it was installed
AGENT="$HOME/Library/LaunchAgents/com.charaka-codes.num.plist"
if [ -f "$AGENT" ]; then
    launchctl unload "$AGENT" 2>/dev/null || true
    rm -f "$AGENT"
    echo "Removed launch agent."
fi

read -p "Also delete usage data in ~/.netmonitor? [y/N] " ans
if [[ "$ans" =~ ^[Yy]$ ]]; then
    rm -rf "$HOME/.netmonitor"
    echo "Usage data deleted."
else
    echo "Usage data kept at ~/.netmonitor"
fi

echo "Done."
