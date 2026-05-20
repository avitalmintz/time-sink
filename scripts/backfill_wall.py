"""Replay every past session to the wall.

For each closed session in sessions.db that hasn't been published yet, we:
  1. Re-aggregate Chrome history for that session's time window
  2. Skip if no visits (Chrome may have purged that window)
  3. Generate the AI headline + opportunity-cost line (one Claude call)
  4. Render text lines (so the wall shows the actual receipt content)
  5. POST to the log server
  6. Mark wall_published = 1 so re-runs don't duplicate

Run:
  source ~/.time-sink.env
  /opt/anaconda3/bin/python3 scripts/backfill_wall.py

Options:
  --min-minutes N   only sessions >= N minutes (default 5)
  --limit N         only do the most recent N sessions
  --dry-run         skip the POST, just show what would happen
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import config, sessions
from src.publish import build_payload, publish_receipt
from src.readers import read_visits
from src.receipt import aggregate, generate_receipt_lines, render_text


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-minutes", type=int, default=5)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="re-publish even sessions marked wall_published=1")
    args = p.parse_args()

    cfg = config.load()
    log_cfg = cfg.get("log_server", {})
    server_url = log_cfg.get("url")
    secret_var = log_cfg.get("secret_env_var", "TIME_SINK_LOG_SECRET")
    secret = os.environ.get(secret_var, "")

    if not args.dry_run and (not server_url or not secret):
        print(f"ERROR: server URL or {secret_var} not set. "
              f"Run `source ~/.time-sink.env` and ensure config.json has log_server.url")
        return 1

    conn = sessions._connect()
    where = "ended_at IS NOT NULL"
    if not args.force:
        where += " AND wall_published = 0"
    query = f"""SELECT id, started_at, ended_at, receipt_printed
                FROM sessions WHERE {where}
                ORDER BY started_at ASC"""
    rows = conn.execute(query).fetchall()

    eligible = []
    for r in rows:
        try:
            start = sessions._parse(r["started_at"])
            end = sessions._parse(r["ended_at"])
        except (ValueError, AttributeError):
            continue
        duration = end - start
        if duration < timedelta(minutes=args.min_minutes):
            continue
        eligible.append((r["id"], start, end, duration))

    if args.limit:
        eligible = eligible[-args.limit:]

    print(f"Found {len(eligible)} sessions to backfill "
          f"(>= {args.min_minutes} minutes each)")
    if args.dry_run:
        print("[dry-run] showing first 5:")
        for sid, s, e, d in eligible[:5]:
            print(f"  session #{sid}  {s.strftime('%Y-%m-%d %H:%M')}  → {e.strftime('%H:%M')}  ({d})")
        return 0

    posted = 0
    skipped = 0
    for sid, start, end, duration in eligible:
        # Re-aggregate from Chrome history for that window
        visits = read_visits(cfg["data_paths"]["chrome_history"], start, end)
        if not visits:
            print(f"  session #{sid}  no visits in Chrome history (likely purged); skip")
            skipped += 1
            continue

        # Need a Session object for aggregate / render_text
        sess = sessions.Session(id=sid, started_at=start, ended_at=end)
        agg = aggregate(sess, visits)
        ai = generate_receipt_lines(agg, duration)
        lines = render_text(sess, agg, cfg,
                            oneliner=ai.get("headline"),
                            opportunity=ai.get("opportunity"))
        payload = build_payload(sess, agg, ai.get("headline"), lines=lines)

        url = publish_receipt(server_url, secret, payload)
        if url:
            print(f"  session #{sid}  {start.strftime('%m-%d %H:%M')}  →  {url}")
            conn.execute("UPDATE sessions SET wall_published = 1 WHERE id = ?", (sid,))
            conn.commit()
            posted += 1
        else:
            print(f"  session #{sid}  PUBLISH FAILED — leaving for retry")
            skipped += 1
        # Gentle rate-limit for Claude API + Render
        time.sleep(0.5)

    print()
    print(f"posted:  {posted}")
    print(f"skipped: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
