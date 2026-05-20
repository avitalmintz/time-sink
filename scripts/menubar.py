"""Menu bar app: toggle TIME SINK tracking on/off with one click.

Run with:
  /opt/anaconda3/bin/python3 scripts/menubar.py

Lives in the menu bar (top-right of screen) until you Quit.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import rumps

from src import config, sessions
from src.tracking_flag import activate, is_active, pause


PRINT_SESSION = REPO_ROOT / "scripts" / "print_session.py"
PYTHON = "/opt/anaconda3/bin/python3"
SARAH_STATE_KEY = "sarah_last_triggered_count"

# Title strings — emoji-free so they render in any environment
TITLE_ON = "● TS"
TITLE_OFF = "○ TS"


class TimeSinkApp(rumps.App):
    def __init__(self):
        super().__init__(name="TS", quit_button=None)
        self._sync_title()
        # Build menu
        self.menu = [
            rumps.MenuItem("Activate tracking", callback=self.on_activate),
            rumps.MenuItem("Pause tracking", callback=self.on_pause),
            None,  # separator
            rumps.MenuItem("Print receipt now", callback=self.on_print_now),
            None,
            rumps.MenuItem("Open archive folder", callback=self.on_open_archive),
            rumps.MenuItem("View tail of log", callback=self.on_view_log),
            None,
            rumps.MenuItem("Quit", callback=rumps.quit_application),
        ]
        self._sync_enabled()

    def _sync_title(self):
        self.title = TITLE_ON if is_active() else TITLE_OFF

    def _sync_enabled(self):
        active = is_active()
        # Disable the action that doesn't apply
        self.menu["Activate tracking"].set_callback(
            None if active else self.on_activate
        )
        self.menu["Pause tracking"].set_callback(
            self.on_pause if active else None
        )

    def on_activate(self, _):
        activate()
        # Reset Sarah baseline to current message count so it counts forward only
        self._reset_sarah_baseline()
        self._sync_title()
        self._sync_enabled()
        # rumps.notification doesn't work under anaconda's Python (no Info.plist).
        # The icon flips to ● TS as visual confirmation.

    def on_pause(self, _):
        pause()
        self._sync_title()
        self._sync_enabled()

    def on_print_now(self, _):
        try:
            subprocess.Popen([PYTHON, str(PRINT_SESSION), "now"],
                             env={**os.environ})
        except Exception as e:
            rumps.alert(title="TS", message=f"Couldn't run: {e}")

    def on_open_archive(self, _):
        subprocess.Popen(["open", str(REPO_ROOT / "out")])

    def on_view_log(self, _):
        log = REPO_ROOT / "data" / "events.log"
        if not log.exists():
            rumps.alert(title="No log yet", message="events.log doesn't exist.")
            return
        # Open in Console.app via `open -a`
        subprocess.Popen(["open", "-a", "Console", str(log)])

    def _reset_sarah_baseline(self):
        """When activating, set Sarah's baseline to current message count
        so it only counts NEW messages from now."""
        cfg = config.load()
        sm = cfg.get("sarah_mode", {})
        if not sm.get("enabled"):
            return
        phone = sm.get("phone")
        if not phone:
            return
        try:
            from src.sarah import read_outgoing
            msgs = read_outgoing(phone)
            sessions.set_state(SARAH_STATE_KEY, str(len(msgs)))
        except Exception as e:
            # Don't block activation if read fails
            print(f"[menubar] couldn't reset Sarah baseline: {e}")


if __name__ == "__main__":
    TimeSinkApp().run()
