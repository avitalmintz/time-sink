"""Aggregate outgoing iMessage activity by contact.

Builds on top of sarah.py's chat.db reader, but instead of filtering to one
number, groups ALL outgoing messages by handle (phone or email) and counts.
Contact names come from config.json's 'contacts' mapping; unmapped handles
are anonymized to the last 4 digits (or first 5 chars of an email).
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CHAT_DB = Path("~/Library/Messages/chat.db").expanduser()
MAC_EPOCH_OFFSET = 978307200


def _mac_to_dt(mac_time: float) -> datetime:
    return datetime.fromtimestamp(
        mac_time / 1_000_000_000 + MAC_EPOCH_OFFSET, tz=timezone.utc
    ).astimezone()


def _dt_to_mac(dt: datetime) -> float:
    return (dt.astimezone(timezone.utc).timestamp() - MAC_EPOCH_OFFSET) * 1_000_000_000


def _anonymize(handle: str) -> str:
    """For unmapped contacts. Phone → last-4 digits. Email → first 4 chars."""
    if not handle:
        return "(unknown)"
    digits = re.sub(r"\D", "", handle)
    if len(digits) >= 4:
        return "...•" + digits[-4:]
    if "@" in handle:
        return handle.split("@")[0][:6] + "@…"
    return handle[:8]


@dataclass
class ContactBatch:
    handle: str           # raw phone/email
    display_name: str     # from config or anonymized
    count: int            # outgoing message count


def read_outgoing_by_contact(
    since: datetime,
    until: datetime,
    contact_map: dict[str, str] | None = None,
    db_path: Path = CHAT_DB,
) -> list[ContactBatch]:
    """Return top contacts by outgoing-message count in [since, until)."""
    if not db_path.exists():
        return []
    contact_map = contact_map or {}

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
        return _query(tmp_path, since, until, contact_map)
    finally:
        tmp_path.unlink(missing_ok=True)
        for ext in ("-shm", "-wal"):
            tmp_path.with_name(tmp_path.name + ext).unlink(missing_ok=True)


def _normalize_phone_for_lookup(handle: str) -> str:
    """Strip non-digits from a phone-shaped handle so config keys can be
    'sarahs phone (305) 401-2415' or '+13054012415' interchangeably."""
    return re.sub(r"\D", "", handle)


def _resolve_name(handle: str, contact_map: dict[str, str]) -> str:
    if not handle:
        return "(unknown)"
    # Try exact match first
    if handle in contact_map:
        return contact_map[handle]
    # Then try digits-only match for phones
    digits = _normalize_phone_for_lookup(handle)
    if digits:
        last10 = digits[-10:]
        for k, v in contact_map.items():
            if _normalize_phone_for_lookup(k)[-10:] == last10:
                return v
    # Email: lowercase compare
    if "@" in handle:
        low = handle.lower()
        for k, v in contact_map.items():
            if k.lower() == low:
                return v
    return _anonymize(handle)


def _query(db_path: Path, since: datetime, until: datetime,
           contact_map: dict[str, str]) -> list[ContactBatch]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    start_mac = _dt_to_mac(since)
    end_mac = _dt_to_mac(until)
    rows = conn.execute(
        """SELECT h.id AS handle
           FROM message m
           JOIN handle h ON m.handle_id = h.ROWID
           WHERE m.is_from_me = 1
             AND m.text IS NOT NULL
             AND m.date >= ?
             AND m.date < ?""",
        (start_mac, end_mac),
    ).fetchall()
    conn.close()

    counts: Counter[str] = Counter()
    for r in rows:
        h = r["handle"] or "(unknown)"
        counts[h] += 1

    out: list[ContactBatch] = []
    for handle, c in counts.most_common():
        out.append(ContactBatch(
            handle=handle,
            display_name=_resolve_name(handle, contact_map),
            count=c,
        ))
    return out
