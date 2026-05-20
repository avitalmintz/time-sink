"""Active-app sampler.

Apple's knowledgeC.db stopped reliably tracking app usage on Sonoma+, so we
keep our own log. The menubar app calls tick() on a 30-second timer; each
call records the current frontmost app's bundle ID. Aggregation rolls those
samples into per-app durations.

Trade-offs vs. knowledgeC.db:
  - We only see app focus, not actual usage time when an app is in background
  - 30s granularity (acceptable for receipt aggregation)
  - Only forward-looking — starts when the menubar runs, no history
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SAMPLE_INTERVAL_SEC = 30
DB_PATH_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "app_log.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    bundle_id TEXT,
    app_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_samples_ts ON app_samples(timestamp);
"""


@dataclass
class AppUsage:
    bundle_id: str
    name: str
    duration_seconds: float


def _connect(path: Path = DB_PATH_DEFAULT) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _get_frontmost() -> tuple[str, str] | tuple[None, None]:
    """Returns (bundle_id, name) for the frontmost macOS app.

    Uses NSWorkspace via PyObjC (bundled with Anaconda's macOS Python).
    Returns (None, None) on any failure.
    """
    try:
        from AppKit import NSWorkspace
        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if front is None:
            return (None, None)
        bundle_id = front.bundleIdentifier()
        name = front.localizedName()
        return (str(bundle_id) if bundle_id else "",
                str(name) if name else "")
    except Exception:
        return (None, None)


def tick(path: Path = DB_PATH_DEFAULT) -> None:
    """Sample the frontmost app and append a row. Safe to call frequently."""
    bundle_id, name = _get_frontmost()
    if not bundle_id:
        return
    conn = _connect(path)
    conn.execute(
        "INSERT INTO app_samples (timestamp, bundle_id, app_name) VALUES (?, ?, ?)",
        (datetime.now().astimezone().isoformat(), bundle_id, name),
    )
    conn.commit()
    conn.close()


def read_app_usage(since: datetime, until: datetime,
                   path: Path = DB_PATH_DEFAULT,
                   sample_interval: float = SAMPLE_INTERVAL_SEC) -> list[AppUsage]:
    """Return per-app durations for [since, until). Each sample counts for
    `sample_interval` seconds. Sorted by duration desc.
    """
    if not path.exists():
        return []
    conn = _connect(path)
    rows = conn.execute(
        """SELECT bundle_id, app_name FROM app_samples
           WHERE timestamp >= ? AND timestamp < ?""",
        (since.astimezone().isoformat(), until.astimezone().isoformat()),
    ).fetchall()
    conn.close()

    counts: Counter[str] = Counter()
    names: dict[str, str] = {}
    for r in rows:
        bid = r["bundle_id"]
        if not bid:
            continue
        counts[bid] += 1
        if r["app_name"]:
            names[bid] = r["app_name"]

    return [
        AppUsage(bundle_id=bid,
                 name=names.get(bid, bid.split(".")[-1]),
                 duration_seconds=c * sample_interval)
        for bid, c in counts.most_common()
    ]
