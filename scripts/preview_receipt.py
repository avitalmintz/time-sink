"""Generate a preview PNG of what the receipt will look like.

Run from repo root:
  python scripts/preview_receipt.py
  python scripts/preview_receipt.py --hours 6     # custom session window
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src import config
from src.readers import read_visits
from src.receipt import (
    aggregate, generate_receipt_lines, render_png, render_text,
)
from src.sessions import Session, latest_session


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=None,
                   help="override session window (look back N hours)")
    p.add_argument("--out", type=Path,
                   default=REPO_ROOT / "out" / "preview.png")
    args = p.parse_args()

    cfg = config.load()

    if args.hours is not None:
        now = datetime.now().astimezone()
        session = Session(
            id=None,
            started_at=now - timedelta(hours=args.hours),
            ended_at=now,
        )
        label = f"--hours {args.hours}"
    else:
        session = latest_session()
        label = "latest session (or synthetic 2hr fallback)"

    until = session.ended_at or datetime.now().astimezone()
    visits = read_visits(cfg["data_paths"]["chrome_history"], session.started_at, until)

    print(f"Session: {label}")
    print(f"  {session.started_at.strftime('%Y-%m-%d %H:%M')}"
          f"  →  {until.strftime('%H:%M')}")
    print(f"  duration: {session.duration}")
    print(f"  visits:   {len(visits)}")

    agg = aggregate(session, visits)
    ai = generate_receipt_lines(agg, session.duration)
    if ai["headline"]:
        print(f"  headline:    {ai['headline']}")
    if ai["opportunity"]:
        print(f"  opportunity: {ai['opportunity']}")
    lines = render_text(session, agg, cfg,
                        oneliner=ai["headline"],
                        opportunity=ai["opportunity"])

    print("\n--- TEXT ---")
    for line in lines:
        print(line)
    print("--- END TEXT ---\n")

    out = render_png(lines, args.out)
    print(f"PNG: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
