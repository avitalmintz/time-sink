"""A simple on/off flag for whether to print receipts.

The flag is just a file's existence: ~/.time-sink-active.
- Exists → tracking is on; sleep/wake hooks generate receipts.
- Doesn't exist → tracking is off; hooks log but skip printing.

The menu bar app toggles this. Other code reads it via is_active().
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

FLAG_PATH = Path.home() / ".time-sink-active"


def is_active() -> bool:
    return FLAG_PATH.exists()


def activate() -> None:
    FLAG_PATH.touch()


def pause() -> None:
    FLAG_PATH.unlink(missing_ok=True)


def toggle() -> bool:
    """Returns the new state (True = active)."""
    if is_active():
        pause()
        return False
    activate()
    return True
