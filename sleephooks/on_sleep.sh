#!/bin/bash
# Called by sleepwatcher on system sleep. Fires print_session.py with 'sleep'.
# Keep this fast — sleepwatcher gives us seconds before the system actually sleeps.

REPO="/Users/avitalmintz/Documents/New project/time-sink"

# Load any secrets (Anthropic API key etc). launchd doesn't load ~/.zshrc.
[ -f "$HOME/.time-sink.env" ] && source "$HOME/.time-sink.env"

exec /opt/anaconda3/bin/python3 "$REPO/scripts/print_session.py" sleep \
    >> "$REPO/data/sleephook.log" 2>&1
