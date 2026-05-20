"""Entry point called by the sleepwatcher hooks.

Usage:
  python scripts/print_session.py sleep     # called on lid-close / system sleep
  python scripts/print_session.py wake      # called on lid-open / system wake
  python scripts/print_session.py now       # ad-hoc; print for current session

Sleep flow:
  1. Mark session ended in sessions.db
  2. Decide whether to print (min duration, min gap-since-last)
  3. Build receipt for the session that just ended
  4. Try to print; if printer unreachable, queue to disk
  5. Save a PNG copy in out/ for archive

Wake flow:
  1. Mark new session started in sessions.db
  2. Try to flush any queued receipts (printer may be back in range)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import os

from src import config, printer, sessions
from src.publish import build_payload, publish_receipt
from src.readers import read_visits
from src.receipt import (
    aggregate, generate_receipt_lines, render_png, render_text,
)
from src import sarah as sarah_mod
from src.episode_receipt import generate_episode_title, render_episode_text
from src.tracking_flag import is_active
from src.screentime import read_app_usage
from src.contacts import read_outgoing_by_contact


LOG_PATH = REPO_ROOT / "data" / "events.log"
SARAH_STATE_KEY = "sarah_last_triggered_count"


def _publish_for_qr(cfg: dict, session, agg, headline: str | None,
                    lines: list[str] | None = None) -> str | None:
    """POST receipt to the log server, return the URL to embed in QR (or None)."""
    log_cfg = cfg.get("log_server", {})
    server_url = log_cfg.get("url")
    secret_var = log_cfg.get("secret_env_var", "TIME_SINK_LOG_SECRET")
    secret = os.environ.get(secret_var, "")
    if not server_url or not secret:
        return None
    payload = build_payload(session, agg, headline, lines=lines)
    return publish_receipt(server_url, secret, payload)


def _log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.now().astimezone().isoformat()}  {msg}\n")
    print(msg, flush=True)


def cmd_sleep(cfg: dict) -> int:
    closed_session = sessions.record_sleep()
    if not closed_session:
        _log("[sleep] no open session to close")
        return 0

    if not is_active():
        _log("[sleep] tracking is paused; skipping (toggle in the menu bar)")
        return 0

    duration = closed_session.duration
    min_dur = timedelta(minutes=cfg["session"].get("min_duration_minutes", 5))
    if duration < min_dur:
        _log(f"[sleep] session {duration} below threshold ({min_dur}); skipping")
        return 0

    # Pull data for the session window
    paths = cfg["data_paths"]
    visits = read_visits(paths["chrome_history"],
                         closed_session.started_at, closed_session.ended_at)
    apps = []
    try:
        apps = read_app_usage(closed_session.started_at, closed_session.ended_at)
    except Exception as e:
        _log(f"[sleep] app usage read failed: {type(e).__name__}: {e}")
    contacts = []
    try:
        contacts = read_outgoing_by_contact(
            closed_session.started_at, closed_session.ended_at,
            contact_map=cfg.get("contacts", {}),
        )
    except Exception as e:
        _log(f"[sleep] contacts read failed: {type(e).__name__}: {e}")

    agg = aggregate(closed_session, visits, apps=apps, contacts=contacts)
    ai = generate_receipt_lines(agg, closed_session.duration)
    lines = render_text(closed_session, agg, cfg,
                        oneliner=ai["headline"], opportunity=ai["opportunity"])

    qr_url = _publish_for_qr(cfg, closed_session, agg, ai["headline"], lines=lines)
    if qr_url:
        _log(f"[sleep] published: {qr_url}")

    archive = REPO_ROOT / "out" / f"receipt_{closed_session.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    render_png(lines, archive)
    _log(f"[sleep] receipt PNG saved: {archive.name}")

    status = printer.send_or_queue(cfg, lines, qr_url=qr_url,
                                    session_id=closed_session.id)
    _log(f"[sleep] receipt {status} (session #{closed_session.id}, "
         f"{len(visits)} visits)")

    if status == "printed":
        sessions.mark_printed(closed_session.id)

    # Piggyback: also check for new Sarah batches on sleep
    _check_sarah(cfg)
    return 0


def cmd_wake(cfg: dict) -> int:
    sid = sessions.record_wake()
    _log(f"[wake] new session #{sid}")
    if not is_active():
        # Still record the wake (so future activation sees a fresh session),
        # but skip any printing/queue activity until tracking is turned on.
        return 0
    # Take the opportunity to flush any queued receipts
    ip = cfg["printer"].get("ip")
    port = cfg["printer"].get("port", 9100)
    if ip:
        printed, remaining = printer.flush_queue(ip, port)
        if printed:
            _log(f"[wake] flushed {printed} queued receipts ({remaining} still queued)")
    # Also check if any new Sarah batches accumulated while we were away
    _check_sarah(cfg)
    return 0


def _check_sarah(cfg: dict) -> int:
    """Check the Sarah message count, fire receipts for any new full batches."""
    if not is_active():
        return 0
    sm = cfg.get("sarah_mode", {})
    if not sm.get("enabled"):
        return 0
    phone = sm.get("phone")
    batch_size = sm.get("batch_size", 10)
    if not phone:
        return 0
    try:
        msgs = sarah_mod.read_outgoing(phone)
    except PermissionError as e:
        _log(f"[sarah] permission error: {e}")
        return 0
    except FileNotFoundError as e:
        _log(f"[sarah] {e}")
        return 0

    try:
        last = int(sessions.get_state(SARAH_STATE_KEY, "0"))
    except ValueError:
        last = 0

    # First-run bootstrap: if we've never triggered before, set the baseline
    # to current count minus the remainder — so we only fire on NEW batches,
    # not retroactively for every batch in the user's whole history.
    if last == 0 and len(msgs) > 0:
        baseline = (len(msgs) // batch_size) * batch_size
        sessions.set_state(SARAH_STATE_KEY, str(baseline))
        _log(f"[sarah] first run: setting baseline to {baseline} "
             f"(of {len(msgs)} total). Future batches will fire.")
        return 0

    batches = sarah_mod.find_unprinted_batches(msgs, last, batch_size)
    if not batches:
        return 0

    fired = 0
    for batch in batches:
        title = generate_episode_title(batch)
        lines = render_episode_text(batch, title, total_to_date=len(msgs), cfg=cfg)
        # Archive PNG
        from src.receipt import render_png
        archive = REPO_ROOT / "out" / f"sarah_{batch.number:04d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        render_png(lines, archive)
        status = printer.send_or_queue(cfg, lines, session_id=None)
        _log(f"[sarah] episode #{batch.number} {status}: '{title or '(no title)'}'")
        fired += 1
        sessions.set_state(SARAH_STATE_KEY, str(batch.number * batch_size))
    return fired


def cmd_sarah(cfg: dict) -> int:
    """Ad-hoc: check for Sarah batches right now without sleeping/waking."""
    fired = _check_sarah(cfg)
    if fired == 0:
        _log("[sarah] no new batches to print")
    return 0


def cmd_sarah_sample(cfg: dict) -> int:
    """Print a sample SARAH receipt using the most recent COMPLETE batch.
    Does not advance the trigger state — purely for previewing the format.
    """
    sm = cfg.get("sarah_mode", {})
    phone = sm.get("phone")
    batch_size = sm.get("batch_size", 10)
    if not phone:
        _log("[sarah-sample] no phone configured")
        return 1
    try:
        msgs = sarah_mod.read_outgoing(phone)
    except PermissionError as e:
        _log(f"[sarah-sample] {e}")
        return 1

    if len(msgs) < batch_size:
        _log(f"[sarah-sample] only {len(msgs)} messages, need {batch_size}")
        return 1

    # Most recent complete batch
    end = (len(msgs) // batch_size) * batch_size
    start = end - batch_size
    batch_msgs = msgs[start:end]
    batch = sarah_mod.Batch(
        number=end // batch_size,
        messages=batch_msgs,
        started_at=batch_msgs[0].timestamp,
        ended_at=batch_msgs[-1].timestamp,
    )
    _log(f"[sarah-sample] generating receipt for batch #{batch.number} "
         f"({batch.started_at.isoformat()} → {batch.ended_at.isoformat()})")

    title = generate_episode_title(batch)
    lines = render_episode_text(batch, title, total_to_date=len(msgs), cfg=cfg)
    from src.receipt import render_png
    archive = REPO_ROOT / "out" / f"sarah_sample_{batch.number:04d}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    render_png(lines, archive)
    status = printer.send_or_queue(cfg, lines, session_id=None)
    _log(f"[sarah-sample] {status}. title: {title!r}")
    return 0


def cmd_now(cfg: dict) -> int:
    """Print a receipt for the current open session right now (no state change)."""
    session = sessions.latest_session()
    until = datetime.now().astimezone()
    visits = read_visits(cfg["data_paths"]["chrome_history"],
                         session.started_at, until)
    apps = []
    try:
        apps = read_app_usage(session.started_at, until)
    except Exception as e:
        _log(f"[now] app usage read failed: {type(e).__name__}: {e}")
    contacts = []
    try:
        contacts = read_outgoing_by_contact(
            session.started_at, until,
            contact_map=cfg.get("contacts", {}),
        )
    except Exception as e:
        _log(f"[now] contacts read failed: {type(e).__name__}: {e}")

    agg = aggregate(session, visits, apps=apps, contacts=contacts)
    ai = generate_receipt_lines(agg, session.duration)
    lines = render_text(session, agg, cfg,
                        oneliner=ai["headline"], opportunity=ai["opportunity"])
    qr_url = _publish_for_qr(cfg, session, agg, ai["headline"], lines=lines)
    if qr_url:
        _log(f"[now] published: {qr_url}")
    status = printer.send_or_queue(cfg, lines, qr_url=qr_url,
                                    session_id=session.id)
    _log(f"[now] receipt {status} (visits={len(visits)})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["sleep", "wake", "now", "sarah", "sarah-sample"])
    args = p.parse_args()
    cfg = config.load()
    try:
        return {
            "sleep": cmd_sleep, "wake": cmd_wake,
            "now": cmd_now, "sarah": cmd_sarah,
            "sarah-sample": cmd_sarah_sample,
        }[args.cmd](cfg)
    except Exception as e:
        _log(f"[error] {args.cmd}: {type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    sys.exit(main())
