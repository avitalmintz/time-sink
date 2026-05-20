"""Read the macOS Address Book and build a phone/email → display-name map.

macOS stores contacts as a set of SQLite databases, one per "source" (local,
iCloud, Google, etc.) at:
    ~/Library/Application Support/AddressBook/Sources/<UUID>/AddressBook-v22.abcddb

We read all of them and merge. Requires Full Disk Access (already granted
for the Messages reader).

Schema (Z-prefixed Core Data convention):
    ZABCDRECORD        — one row per contact
    ZABCDPHONENUMBER   — phones; ZOWNER -> ZABCDRECORD.Z_PK
    ZABCDEMAILADDRESS  — emails; ZOWNER -> ZABCDRECORD.Z_PK
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

ADDRESSBOOK_ROOT = Path("~/Library/Application Support/AddressBook").expanduser()


def _normalize_phone_last10(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\D", "", s)[-10:]


def _display_name_from_row(row) -> str | None:
    fn = (row["ZFIRSTNAME"] or "").strip() if row["ZFIRSTNAME"] else ""
    ln = (row["ZLASTNAME"] or "").strip() if row["ZLASTNAME"] else ""
    nick = (row["ZNICKNAME"] or "").strip() if "ZNICKNAME" in row.keys() and row["ZNICKNAME"] else ""
    org = (row["ZORGANIZATION"] or "").strip() if "ZORGANIZATION" in row.keys() and row["ZORGANIZATION"] else ""

    # Prefer first name only — fits better on a receipt and less identifying.
    # Fall back to last name, then nickname, then organization.
    if fn:
        # If two friends share a first name, distinguish with last initial.
        return fn
    if nick:
        return nick
    if ln:
        return ln
    if org:
        return org
    return None


def _read_one_db(db_path: Path) -> dict[str, str]:
    """Returns {handle → display name} for one address book source."""
    out: dict[str, str] = {}
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            shutil.copy2(db_path, tmp_path)
        except (PermissionError, OSError):
            return out
        for ext in ("-shm", "-wal"):
            src = db_path.with_name(db_path.name + ext)
            if src.exists():
                try:
                    shutil.copy2(src, tmp_path.with_name(tmp_path.name + ext))
                except (PermissionError, OSError):
                    pass

        try:
            conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.OperationalError:
            return out

        # Discover available columns on ZABCDRECORD (varies by macOS version)
        try:
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(ZABCDRECORD)").fetchall()}
        except sqlite3.OperationalError:
            conn.close()
            return out

        select_cols = ["Z_PK", "ZFIRSTNAME", "ZLASTNAME"]
        for opt in ("ZNICKNAME", "ZORGANIZATION"):
            if opt in cols:
                select_cols.append(opt)

        records: dict[int, str] = {}
        try:
            for r in conn.execute(
                f"SELECT {', '.join(select_cols)} FROM ZABCDRECORD"
            ):
                name = _display_name_from_row(r)
                if name:
                    records[r["Z_PK"]] = name
        except sqlite3.OperationalError:
            pass

        # Phones
        try:
            for r in conn.execute(
                "SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"
            ):
                pk = r["ZOWNER"]
                if pk in records:
                    digits = _normalize_phone_last10(r["ZFULLNUMBER"])
                    if digits:
                        out[digits] = records[pk]
        except sqlite3.OperationalError:
            pass

        # Emails
        try:
            for r in conn.execute(
                "SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"
            ):
                pk = r["ZOWNER"]
                if pk in records and r["ZADDRESS"]:
                    out[r["ZADDRESS"].lower()] = records[pk]
        except sqlite3.OperationalError:
            pass

        conn.close()
        return out
    finally:
        tmp_path.unlink(missing_ok=True)
        for ext in ("-shm", "-wal"):
            tmp_path.with_name(tmp_path.name + ext).unlink(missing_ok=True)


def build_contact_map() -> dict[str, str]:
    """Returns {handle_key → display_name}. Handle key is:
      - last 10 digits of phone (normalized)
      - or lowercased email
    Safe to call even if no addressbook DBs are readable; returns {} then.
    """
    if not ADDRESSBOOK_ROOT.exists():
        return {}
    merged: dict[str, str] = {}
    for db in ADDRESSBOOK_ROOT.glob("Sources/*/AddressBook-v22.abcddb"):
        try:
            merged.update(_read_one_db(db))
        except Exception:
            continue
    return merged
