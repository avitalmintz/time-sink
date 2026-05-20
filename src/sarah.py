"""Sarah mode — count outgoing messages to a specific phone number,
fire a receipt every Nth message.

Reads ~/Library/Messages/chat.db. Requires Full Disk Access permission on
your Terminal app (System Settings → Privacy & Security → Full Disk Access).
Without it, sqlite3 returns 'unable to open database file'.

Privacy: only outgoing messages (is_from_me=1) are read for content. Sarah's
replies are counted/timed but their text is not used. The text we DO read
(yours) is sent to Anthropic for the episode-title generation.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CHAT_DB_PATH = Path("~/Library/Messages/chat.db").expanduser()
# macOS absolute time epoch: 2001-01-01 00:00:00 UTC
MAC_EPOCH_OFFSET = 978307200


def normalize_phone(s: str) -> str:
    """Strip everything except digits."""
    return re.sub(r"\D", "", s or "")


def _phone_matches(handle: str, target_digits: str) -> bool:
    """A handle like '+13054012415' or '(305) 401-2415' matches if the
    digit suffix matches target_digits (last 10 digits)."""
    h = normalize_phone(handle)
    if not h:
        return False
    # match on last 10 digits, accommodating +1 country code
    return h[-10:] == target_digits[-10:]


def _mac_time_to_dt(mac_time: int) -> datetime:
    """chat.db's `date` column is nanoseconds since 2001-01-01 UTC."""
    seconds = mac_time / 1_000_000_000 + MAC_EPOCH_OFFSET
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone()


@dataclass
class Msg:
    rowid: int
    text: str
    timestamp: datetime
    is_from_me: bool


def read_outgoing(phone: str, db_path: Path = CHAT_DB_PATH) -> list[Msg]:
    """Return all outgoing messages to the given phone number, oldest first."""
    if not db_path.exists():
        raise FileNotFoundError(f"chat.db not found at {db_path}")
    target = normalize_phone(phone)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        # Messages also keeps SHM/WAL files; we want a clean read so copy
        # all related files if present.
        try:
            shutil.copy2(db_path, tmp_path)
        except PermissionError as e:
            raise PermissionError(
                "Can't read ~/Library/Messages/chat.db. Grant Full Disk "
                "Access to your Terminal app:\n"
                "  System Settings → Privacy & Security → Full Disk Access "
                "→ + → Terminal.app\n"
                "Then quit and re-open Terminal."
            ) from e
        for ext in ("-shm", "-wal"):
            src = db_path.with_name(db_path.name + ext)
            if src.exists():
                try:
                    shutil.copy2(src, tmp_path.with_name(tmp_path.name + ext))
                except PermissionError:
                    pass
        try:
            return _read(tmp_path, target)
        except sqlite3.OperationalError as e:
            if "unable to open" in str(e).lower() or "authorization" in str(e).lower():
                raise PermissionError(
                    "Can't open chat.db copy. Full Disk Access may still be "
                    "propagating — try quitting Terminal and re-opening."
                ) from e
            raise
    finally:
        tmp_path.unlink(missing_ok=True)
        for ext in ("-shm", "-wal"):
            tmp_path.with_name(tmp_path.name + ext).unlink(missing_ok=True)


def _read(db_path: Path, target_digits: str) -> list[Msg]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # First find handle IDs whose id matches our number (last 10 digits)
    handles = conn.execute("SELECT ROWID, id FROM handle").fetchall()
    handle_ids = [h["ROWID"] for h in handles if _phone_matches(h["id"], target_digits)]
    if not handle_ids:
        conn.close()
        return []

    qmarks = ",".join("?" * len(handle_ids))
    rows = conn.execute(
        f"""SELECT ROWID, text, date, is_from_me
            FROM message
            WHERE handle_id IN ({qmarks})
              AND is_from_me = 1
              AND text IS NOT NULL
              AND text != ''
            ORDER BY date ASC""",
        handle_ids,
    ).fetchall()
    conn.close()

    return [
        Msg(
            rowid=r["ROWID"],
            text=r["text"],
            timestamp=_mac_time_to_dt(r["date"]),
            is_from_me=True,
        )
        for r in rows
    ]


@dataclass
class Batch:
    number: int               # batch #1 = msgs 1-10, batch #2 = msgs 11-20, ...
    messages: list[Msg]
    started_at: datetime
    ended_at: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()


def find_unprinted_batches(messages: list[Msg], last_triggered: int,
                            batch_size: int = 10) -> list[Batch]:
    """Find every full batch of N messages past the last-triggered count.

    If the user sent 47 messages and we've triggered through 30, we return
    batches #4 (msgs 31-40). Msg 41-47 stay pending until they hit 50.
    """
    current = len(messages)
    batches: list[Batch] = []
    n = batch_size
    next_threshold = last_triggered + n
    while current >= next_threshold:
        batch_msgs = messages[next_threshold - n : next_threshold]
        batches.append(Batch(
            number=next_threshold // n,
            messages=batch_msgs,
            started_at=batch_msgs[0].timestamp,
            ended_at=batch_msgs[-1].timestamp,
        ))
        next_threshold += n
    return batches
