"""Render a SARAH-mode episode receipt for a batch of 10 outgoing messages.

Uses the same look-and-feel building blocks as the session receipt (barcode,
monospace, 48-char width) so the wall of receipts has visual unity.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .receipt import (
    CHAR_WIDTH, _barcode, _sanitize, _transaction_id, _truncate, _hms,
    render_png,
)
from .sarah import Batch


def _hm(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m} min"


def generate_episode_title(batch: Batch, timeout_sec: float = 4.0) -> str | None:
    """Ask Claude for a one-line 'episode title' summarizing the outgoing batch."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    msgs_text = "\n".join(f"- {m.text}" for m in batch.messages)
    prompt = f"""You write episode titles for the user's text conversations, like sitcom episode titles. Read the batch of 10 outgoing messages below and produce ONE title.

Style:
- Start with "The One Where..." or "The One About..." or "The One With..." — pick whichever fits
- Title-case the substantive words
- Under 60 characters total
- Be specific to the actual content (a topic, an event, a recurring word) — not generic
- No quotes, no period at end, just the title
- If the batch is gibberish or unclear, write a plausibly mundane title

Examples of good titles:
- The One Where We Tried To Pick A Restaurant
- The One About Maya's Breakup
- The One With Too Many Voice Memos
- The One Where No One Could Agree On A Time

Outgoing messages (only the user's side — Sarah's replies are not shown):
{msgs_text}

Output just the title, nothing else."""

    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 120,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read())
        text = data["content"][0]["text"].strip()
        # Cleanup: remove any wrapping quotes/period
        text = text.strip("'\"`. ")
        if len(text) > 90:
            text = text[:87].rstrip() + "..."
        return text or None
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
            json.JSONDecodeError, TimeoutError) as e:
        print(f"  [episode title skipped] {type(e).__name__}: {e}")
        return None


def _wrap_quote(text: str, width: int) -> list[str]:
    """Wrap a single line into multiple, centered. Adds matching quotes."""
    text = text.strip()
    if not text:
        return []
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if len(candidate) > width - 4:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    if not lines:
        return []
    # Add quotation marks around the whole quote
    lines[0] = '"' + lines[0]
    lines[-1] = lines[-1] + '"'
    return lines


def render_episode_text(batch: Batch, title: str | None,
                        total_to_date: int, cfg: dict) -> list[str]:
    target_name = cfg.get("sarah_mode", {}).get("name", "SARAH")
    owner = cfg.get("receipt", {}).get("owner", "")
    rule = "-" * CHAR_WIDTH
    seed = f"sarah_{batch.number}_{batch.started_at.isoformat()}"

    lines: list[str] = []
    lines.append(_barcode(seed))
    lines.append("")
    lines.append(target_name.center(CHAR_WIDTH))
    lines.append(f"episode {batch.number:04d}".center(CHAR_WIDTH))
    lines.append(rule)
    txn = f"NO. {_transaction_id(seed)}"
    date_str = batch.started_at.strftime("%Y-%m-%d")
    lines.append(f"  {txn}{date_str.rjust(CHAR_WIDTH - 4 - len(txn))}")
    lines.append(rule)
    lines.append("")

    # The episode title — centerpiece
    if title:
        for ql in _wrap_quote(title, CHAR_WIDTH):
            lines.append(ql.center(CHAR_WIDTH))
    else:
        lines.append("(no title generated)".center(CHAR_WIDTH))
    lines.append("")

    # Stats: count, span, duration
    lines.append(f"{len(batch.messages)} messages sent".center(CHAR_WIDTH))
    span = (batch.started_at.strftime('%-I:%M %p') + " — "
            + batch.ended_at.strftime('%-I:%M %p'))
    lines.append(span.center(CHAR_WIDTH))
    lines.append(f"({_hm(batch.duration_seconds)})".center(CHAR_WIDTH))
    lines.append("")

    lines.append(rule)
    lines.append("")
    lines.append("TOTAL MESSAGES TO DATE".center(CHAR_WIDTH))
    lines.append(f"{total_to_date:,}".center(CHAR_WIDTH))
    lines.append("")
    lines.append(rule)
    lines.append("")

    if owner:
        lines.append(owner.center(CHAR_WIDTH))
        lines.append("")
    lines.append(_barcode(seed[::-1]))
    lines.append("")
    return [_sanitize(line) for line in lines]
