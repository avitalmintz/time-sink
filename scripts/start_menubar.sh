#!/bin/bash
# Launch the TIME SINK menu bar app in the background.
# Run this once after rebooting / logging in to have the toggle available.

REPO="/Users/avitalmintz/Documents/New project/time-sink"
[ -f "$HOME/.time-sink.env" ] && source "$HOME/.time-sink.env"

# Kill any prior instance
pkill -f "scripts/menubar.py" 2>/dev/null

# Launch detached so it survives the terminal closing
nohup /opt/anaconda3/bin/python3.12 "$REPO/scripts/menubar.py" \
    >> "$REPO/data/menubar.log" 2>&1 &

echo "menu bar app launched (PID $!)"
echo "look for ● TS or ○ TS in the top-right of your menu bar"
