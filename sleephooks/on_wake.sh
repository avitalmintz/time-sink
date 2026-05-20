#!/bin/bash
# Called by sleepwatcher on system wake. Records the wake event and tries
# to drain the print queue (in case the printer is reachable now).

REPO="/Users/avitalmintz/Documents/New project/time-sink"

[ -f "$HOME/.time-sink.env" ] && source "$HOME/.time-sink.env"

exec /opt/anaconda3/bin/python3 "$REPO/scripts/print_session.py" wake \
    >> "$REPO/data/sleephook.log" 2>&1
