"""Read macOS Screen Time data from knowledgeC.db.

knowledgeC.db is at ~/Library/Application Support/Knowledge/knowledgeC.db
and requires Full Disk Access (already granted for the Messages reader).

Times are macOS absolute time (seconds since 2001-01-01 UTC). We convert
them to local-aware datetimes the same way as sarah.py does for Messages.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KNOWLEDGE_DB = Path("~/Library/Application Support/Knowledge/knowledgeC.db").expanduser()
# macOS absolute time epoch: 2001-01-01 00:00:00 UTC
MAC_EPOCH_OFFSET = 978307200


def _to_mac_time(dt: datetime) -> float:
    return dt.astimezone(timezone.utc).timestamp() - MAC_EPOCH_OFFSET


# Map bundle ID → human-readable display name. Falls back to bundle suffix
# if unknown.
APP_DISPLAY_NAMES = {
    "com.google.Chrome": "Google Chrome",
    "com.apple.Safari": "Safari",
    "org.mozilla.firefox": "Firefox",
    "com.spotify.client": "Spotify",
    "com.apple.Music": "Apple Music",
    "com.apple.mail": "Mail",
    "com.apple.iCal": "Calendar",
    "com.apple.Notes": "Notes",
    "com.apple.iChat": "Messages",
    "com.apple.MobileSMS": "Messages",
    "com.apple.MessagesViewService": "Messages",
    "com.apple.Pages": "Pages",
    "com.apple.Numbers": "Numbers",
    "com.apple.Keynote": "Keynote",
    "com.apple.Preview": "Preview",
    "com.apple.finder": "Finder",
    "com.apple.systempreferences": "System Settings",
    "com.apple.Terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm",
    "com.microsoft.VSCode": "VS Code",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.hnc.Discord": "Discord",
    "com.zoom.xos": "Zoom",
    "us.zoom.xos": "Zoom",
    "com.figma.Desktop": "Figma",
    "notion.id": "Notion",
    "com.linear": "Linear",
    "com.spotify.client": "Spotify",
    "com.apple.Photos": "Photos",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.openai.chat": "ChatGPT",
    "claude.app": "Claude",
    "com.anthropic.claudefordesktop": "Claude",
    "com.electron.clipboard-manager": "Clipboard Manager",
    "com.todesktop.230313mzl4w4u92": "Claude",
    "com.tdesktop": "Telegram",
    "ru.keepcoder.Telegram": "Telegram",
    "com.facebook.archon.developerID": "Messenger",
    "com.facebook.archon": "Messenger",
    "company.thebrowser.Browser": "Arc",
    "company.thebrowser.dia": "Dia",
}


def _display_name(bundle_id: str) -> str:
    if bundle_id in APP_DISPLAY_NAMES:
        return APP_DISPLAY_NAMES[bundle_id]
    # Fall back to last segment of bundle ID
    parts = bundle_id.split(".")
    return parts[-1].replace("-", " ").title() if parts else bundle_id


@dataclass
class AppUsage:
    bundle_id: str
    name: str
    duration_seconds: float


def read_app_usage(since: datetime, until: datetime,
                   db_path: Path = KNOWLEDGE_DB) -> list[AppUsage]:
    """Return per-app cumulative durations for the time window, sorted desc."""
    if not db_path.exists():
        return []
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            shutil.copy2(db_path, tmp_path)
        except PermissionError:
            return []
        for ext in ("-shm", "-wal"):
            src = db_path.with_name(db_path.name + ext)
            if src.exists():
                try:
                    shutil.copy2(src, tmp_path.with_name(tmp_path.name + ext))
                except PermissionError:
                    pass
        return _query(tmp_path, since, until)
    finally:
        tmp_path.unlink(missing_ok=True)
        for ext in ("-shm", "-wal"):
            tmp_path.with_name(tmp_path.name + ext).unlink(missing_ok=True)


def _query(db_path: Path, since: datetime, until: datetime) -> list[AppUsage]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    start = _to_mac_time(since)
    end = _to_mac_time(until)
    rows = conn.execute(
        """SELECT ZVALUESTRING AS bundle_id,
                  ZSTARTDATE AS start_time,
                  ZENDDATE AS end_time
           FROM ZOBJECT
           WHERE ZSTREAMNAME = '/app/usage'
             AND ZVALUESTRING IS NOT NULL
             AND ZENDDATE >= ?
             AND ZSTARTDATE < ?""",
        (start, end),
    ).fetchall()
    conn.close()
    totals: Counter[str] = Counter()
    for r in rows:
        s = max(r["start_time"], start)
        e = min(r["end_time"], end)
        dur = e - s
        if dur <= 0:
            continue
        totals[r["bundle_id"]] += dur
    out = [
        AppUsage(bundle_id=bid, name=_display_name(bid), duration_seconds=sec)
        for bid, sec in totals.most_common()
    ]
    return out
