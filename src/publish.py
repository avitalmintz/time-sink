"""POST a receipt to the time-sink-log server, get back a roast URL.

The URL gets baked into a QR on the printed receipt. If the publish fails
(network issues, server down), we return None and the receipt still prints
— just without the QR. Never blocks the print.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def publish_receipt(server_url: str, secret: str, data: dict,
                    timeout: float = 5.0) -> str | None:
    if not server_url or not secret:
        return None
    try:
        req = urllib.request.Request(
            f"{server_url.rstrip('/')}/receipts",
            data=json.dumps(data).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Time-Sink-Secret": secret,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read())
        return d.get("url")
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError, OSError) as e:
        print(f"  [publish failed] {type(e).__name__}: {e}")
        return None


def build_payload(session, agg, headline: str | None,
                  lines: list[str] | None = None) -> dict:
    """Shape the aggregated session into the payload the server expects.

    If `lines` is given (the rendered text-line receipt), it's included so
    the server's /wall page can show the actual printed receipt visually.
    """
    from .receipt import _hms
    payload = {
        "date": session.started_at.astimezone().strftime("%Y-%m-%d"),
        "duration_str": _hms(agg.duration.total_seconds()),
        "tab_time_str": _hms(agg.total_active_seconds),
        "pages_loaded": agg.visit_count,
        "oneliner": headline,
        "categories": [
            (k, _hms(v))
            for k, v in agg.category_seconds.items()
            if v > 60
        ],
        "top_sites": agg.domain_counts[:10],
        "top_pages": [
            (p.title, p.domain, p.count) for p in agg.top_pages[:8]
        ],
        "searches": agg.searches[:10],
        "longest_domain": agg.longest_domain[0] if agg.longest_domain else None,
        "longest_time_str": _hms(agg.longest_domain[1]) if agg.longest_domain else None,
    }
    if lines is not None:
        payload["lines"] = lines
    return payload
