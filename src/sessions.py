"""Session boundary tracking.

A session is the span between wake and sleep events. We persist them to
a tiny SQLite db so the receipt-generation script can ask "what was the
last session?" regardless of when it runs.

Bootstrap: if the db is empty (e.g., we just installed) and someone asks
for "the most recent session," we synthesize a single open session that
starts 2 hours before now. This lets us generate preview receipts before
the sleepwatcher hooks are even installed.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "sessions.db"


def _parse(s: str) -> datetime:
    """Parse an ISO timestamp. Naive strings are treated as UTC (SQLite's
    default), then converted to local. Aware strings pass through."""
    dt = datetime.fromisoformat(s.replace(" ", "T"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone()
    return dt

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  receipt_printed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);

CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_state(key: str, default: str = "", path: Path = DB_PATH_DEFAULT) -> str:
    conn = _connect(path)
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(key: str, value: str, path: Path = DB_PATH_DEFAULT) -> None:
    conn = _connect(path)
    conn.execute(
        """INSERT INTO state (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
             value = excluded.value, updated_at = excluded.updated_at""",
        (key, value),
    )
    conn.commit()


@dataclass
class Session:
    id: int | None
    started_at: datetime
    ended_at: datetime | None
    receipt_printed: bool = False

    @property
    def duration(self) -> timedelta:
        end = self.ended_at or datetime.now().astimezone()
        start = self.started_at
        # Coerce naive timestamps (e.g., from raw SQL) to local tz so the
        # subtraction doesn't blow up.
        if start.tzinfo is None:
            start = start.astimezone()
        if end.tzinfo is None:
            end = end.astimezone()
        return end - start


def _connect(path: Path = DB_PATH_DEFAULT) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "wall_published" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN wall_published INTEGER DEFAULT 0")
        conn.commit()


def record_wake(at: datetime | None = None, path: Path = DB_PATH_DEFAULT) -> int:
    """Open a new session."""
    at = (at or datetime.now().astimezone())
    conn = _connect(path)
    # Close any dangling open session first
    conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE ended_at IS NULL",
        (at.isoformat(),),
    )
    cur = conn.execute(
        "INSERT INTO sessions (started_at) VALUES (?)",
        (at.isoformat(),),
    )
    conn.commit()
    return cur.lastrowid


def record_sleep(at: datetime | None = None, path: Path = DB_PATH_DEFAULT) -> Session | None:
    """Close the current open session, return it."""
    at = (at or datetime.now().astimezone())
    conn = _connect(path)
    row = conn.execute(
        "SELECT id, started_at FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE id = ?",
        (at.isoformat(), row["id"]),
    )
    conn.commit()
    return Session(
        id=row["id"],
        started_at=_parse(row["started_at"]),
        ended_at=at,
    )


def latest_session(path: Path = DB_PATH_DEFAULT, fallback_hours: int = 2) -> Session:
    """Return the most recent session.

    If no records exist, synthesize a session covering the last `fallback_hours`
    so we can generate preview receipts before sleepwatcher is installed.
    """
    conn = _connect(path)
    row = conn.execute(
        "SELECT id, started_at, ended_at, receipt_printed FROM sessions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        now = datetime.now().astimezone()
        return Session(
            id=None,
            started_at=now - timedelta(hours=fallback_hours),
            ended_at=now,
        )
    return Session(
        id=row["id"],
        started_at=_parse(row["started_at"]),
        ended_at=_parse(row["ended_at"]) if row["ended_at"] else None,
        receipt_printed=bool(row["receipt_printed"]),
    )


def mark_printed(session_id: int, path: Path = DB_PATH_DEFAULT) -> None:
    conn = _connect(path)
    conn.execute("UPDATE sessions SET receipt_printed = 1 WHERE id = ?", (session_id,))
    conn.commit()
