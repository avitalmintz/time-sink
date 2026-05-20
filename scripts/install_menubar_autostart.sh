#!/bin/bash
# Install the menu bar app as a LaunchAgent so it auto-starts at login
# and respawns if it crashes.

set -e

REPO="/Users/avitalmintz/Documents/New project/time-sink"
PLIST_SRC="$REPO/scripts/com.avitalmintz.timesink.menubar.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.avitalmintz.timesink.menubar.plist"

mkdir -p "$HOME/Library/LaunchAgents"

# Stop any prior instance first
launchctl unload "$PLIST_DEST" 2>/dev/null || true
pkill -f "scripts/menubar.py" 2>/dev/null || true

# Copy + load
cp "$PLIST_SRC" "$PLIST_DEST"
launchctl load "$PLIST_DEST"

echo "Installed. The menu bar app now starts on login + respawns if it crashes."
echo "Look for ● TS or ○ TS in your menu bar (top-right of screen)."
echo ""
echo "To uninstall:  launchctl unload $PLIST_DEST && rm $PLIST_DEST"
