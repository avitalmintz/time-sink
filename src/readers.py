"""Read browser history + clipboard activity for a time window.

Both source DBs are locked while their apps run, so we copy to /tmp first
and read from there. Same approach as the old sin-eater code.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse


# ---- domain helpers ----

def extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def extract_google_query(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if "google" not in parsed.netloc:
        return None
    q = parse_qs(parsed.query).get("q")
    if not q:
        return None
    s = q[0].strip()
    return s or None


def chrome_time_to_dt(chrome_time: int) -> datetime | None:
    """Chrome stores time as microseconds since 1601-01-01 UTC."""
    if chrome_time == 0:
        return None
    seconds = chrome_time / 1_000_000 - 11644473600
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def dt_to_chrome_time(dt: datetime) -> int:
    seconds = dt.astimezone(timezone.utc).timestamp() + 11644473600
    return int(seconds * 1_000_000)


# ---- data structures ----

@dataclass
class Visit:
    timestamp: datetime
    domain: str
    url: str
    title: str | None
    duration_seconds: float

@dataclass
class Copy:
    timestamp: datetime
    source_app: str | None
    source_domain: str | None
    preview: str | None  # short snippet only, never raw content


# ---- readers ----

def read_visits(history_path: Path, since: datetime, until: datetime) -> list[Visit]:
    if not history_path.exists():
        return []
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(history_path, tmp_path)
        return _read_visits_from(tmp_path, since, until)
    finally:
        tmp_path.unlink(missing_ok=True)


def _read_visits_from(db_path: Path, since: datetime, until: datetime) -> list[Visit]:
    chrome_since = dt_to_chrome_time(since)
    chrome_until = dt_to_chrome_time(until)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT v.visit_time, v.visit_duration, u.url, u.title
           FROM visits v JOIN urls u ON u.id = v.url
           WHERE v.visit_time >= ? AND v.visit_time < ?
           ORDER BY v.visit_time ASC""",
        (chrome_since, chrome_until),
    ).fetchall()
    out: list[Visit] = []
    for r in rows:
        dt = chrome_time_to_dt(r["visit_time"])
        if dt is None:
            continue
        domain = extract_domain(r["url"])
        if not domain:
            continue
        out.append(Visit(
            timestamp=dt,
            domain=domain,
            url=r["url"],
            title=r["title"] or None,
            duration_seconds=r["visit_duration"] / 1_000_000,
        ))
    conn.close()
    return out


def read_copies(clipboard_path: Path, since: datetime, until: datetime) -> list[Copy]:
    if not clipboard_path.exists():
        return []
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(clipboard_path, tmp_path)
        return _read_copies_from(tmp_path, since, until)
    finally:
        tmp_path.unlink(missing_ok=True)


def _read_copies_from(db_path: Path, since: datetime, until: datetime) -> list[Copy]:
    # Clipboard Manager stores created_at as local-time 'YYYY-MM-DD HH:MM:SS'
    since_str = since.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    until_str = until.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT type, preview, created_at, source_app, source_url
           FROM entries
           WHERE created_at >= ? AND created_at < ?
           ORDER BY created_at ASC""",
        (since_str, until_str),
    ).fetchall()
    out: list[Copy] = []
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["created_at"].replace(" ", "T")).astimezone()
        except (ValueError, AttributeError):
            continue
        out.append(Copy(
            timestamp=dt,
            source_app=r["source_app"],
            source_domain=extract_domain(r["source_url"]),
            preview=(r["preview"] or "")[:50] if r["type"] == "text" else "(image)",
        ))
    conn.close()
    return out
