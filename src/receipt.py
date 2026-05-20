"""Render a receipt for one session.

Output:
  - render_text(session, agg, cfg) -> list[str]   (what we'll send to the printer)
  - render_png(lines, path)                       (preview for design iteration)

The PNG is generated at the printer's actual pixel width (576 px for 80mm at
203 DPI) using a monospace font.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

from .categorize import (
    CATEGORIES_ORDERED, categorize, web_category_kind, app_kind,
)
from .readers import Visit, extract_google_query
from .sessions import Session

# 80mm thermal printer rendering constants
PAPER_WIDTH_PX = 576
LINE_HEIGHT_PX = 20
MARGIN_PX = 16
FONT_SIZE = 14

FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/System/Library/Fonts/Courier New.ttf",
]

CHAR_WIDTH = 48

# Map of Unicode chars that don't render on most thermal printers
# to ASCII equivalents. Apply via _sanitize() before rendering.
_ASCII_FALLBACKS = {
    "─": "-",
    "—": "-",
    "–": "-",
    "…": "...",
    "→": "->",
    "←": "<-",
    "•": "*",
    "·": ".",
    """: '"',
    """: '"',
    "'": "'",
    "'": "'",
    "█": "|",
    "▓": "|",
    "▒": ":",
    "░": ".",
}


def _sanitize(s: str) -> str:
    for u, a in _ASCII_FALLBACKS.items():
        s = s.replace(u, a)
    return s


# ---- helpers ----

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _barcode(seed: str, width: int = CHAR_WIDTH) -> str:
    """ASCII bar/space pattern. Looks more like a real barcode than
    Unicode blocks — and thermal printers reliably support it."""
    h = hashlib.sha256(seed.encode()).digest()
    out: list[str] = []
    pos = 0
    i = 0
    on = True
    while pos < width:
        b = h[i % len(h)]
        chunk = (b & 0b11) + 1
        ch = "|" if on else " "
        out.append(ch * chunk)
        pos += chunk
        on = not on
        i += 1
    return "".join(out)[:width]


def _transaction_id(seed: str) -> str:
    h = hashlib.sha256(seed.encode()).digest()
    n = int.from_bytes(h[:4], "big") % 100000
    return f"{n:05d}"


# ---- opportunity-cost activities ----

# (minutes-needed, activity-text). Pick the largest whose minutes <= session.
# If multiple are eligible we randomly choose for variety.
ACTIVITIES: list[tuple[int, str]] = [
    (2, "drunk a glass of water"),
    (2, "sent a real reply to a text"),
    (3, "made your bed"),
    (3, "watered all your plants"),
    (4, "brushed your teeth properly"),
    (5, "stretched"),
    (5, "meditated"),
    (6, "made a real espresso"),
    (7, "boiled and eaten an egg"),
    (8, "showered"),
    (10, "walked around the block"),
    (10, "washed the dishes"),
    (12, "called your mom"),
    (12, "made breakfast"),
    (15, "walked a mile"),
    (15, "written a journal entry"),
    (18, "cleared your inbox"),
    (20, "done a yoga sequence"),
    (20, "made and eaten a salad"),
    (25, "called your grandma"),
    (30, "cooked a real meal"),
    (30, "read a whole chapter"),
    (40, "watched a documentary short"),
    (45, "gone for a real run"),
    (50, "cleaned the kitchen and the bathroom"),
    (60, "watched a sitcom"),
    (60, "read 60 pages"),
    (60, "walked from Hyde Park to the Loop"),
    (75, "run a 10K"),
    (90, "watched a movie"),
    (90, "read 90 pages without checking your phone"),
    (120, "read half a book"),
    (120, "baked bread from scratch"),
    (150, "driven to Milwaukee"),
    (180, "watched The Godfather"),
    (180, "called every member of your family"),
    (210, "read all of Catcher in the Rye"),
    (240, "read a whole short novel"),
    (300, "flown to New York"),
    (360, "slept a full night"),
    (480, "worked a full shift"),
]


def opportunity_cost(duration: timedelta, seed: str = "") -> str:
    minutes = int(duration.total_seconds() // 60)
    eligible = [(m, t) for m, t in ACTIVITIES if m <= minutes]
    if not eligible:
        return ""
    eligible.sort(key=lambda x: x[0])
    largest = eligible[-1][0]
    # Pick from the top 25% — activities that are close to the user's
    # actual duration so "in X you could have done Y" reads honestly.
    cutoff_min = max(largest * 0.75, 1)
    pool = [t for m, t in eligible if m >= cutoff_min]
    rng = random.Random(seed)
    return rng.choice(pool)


# ---- generative receipt content (one Claude call, two fields) ----

def generate_receipt_lines(agg: "Aggregated", duration: timedelta,
                            timeout_sec: float = 5.0) -> dict[str, str | None]:
    """One Claude call → both the session headline and the opportunity-cost
    activity, sharing context for cohesion.

    Returns {'headline': str|None, 'opportunity': str|None}. Either may be
    None if the API call fails. Callers should fall back to the static
    ACTIVITIES list for opportunity when None.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"headline": None, "opportunity": None}

    sites = ", ".join(f"{d} (x{c})" for d, c in agg.domain_counts[:6])
    searches = "; ".join(agg.searches[:5]) or "(none)"
    cats = ", ".join(
        f"{k}:{_hms(v)}" for k, v in agg.category_seconds.items() if v > 60
    )
    mins = int(duration.total_seconds() // 60)

    drift_min = int(getattr(agg, "drift_seconds", 0) // 60)
    intent_min = int(getattr(agg, "intent_seconds", 0) // 60)
    apps_text = ", ".join(
        f"{a.name} {int(a.duration_seconds // 60)}m"
        for a in getattr(agg, "apps", [])[:6]
    ) or "(none captured)"

    prompt = f"""You write two short lines for a thermal-printer receipt about one browser/computer session. Tone: HONESTLY OBSERVATIONAL. Pointed about the actual patterns in the data. Not preachy. Not cringe. Not cruel. The voice of someone who reads the data and names what's there — including the gap between what the user might say they did and what the data shows.

Return BOTH lines as a JSON object: {{"headline": "...", "opportunity": "..."}}

HEADLINE — one line summarizing what this session WAS, with specificity. Lowercase. Under 60 chars. No period. No quotes inside the string. Call out specific patterns: things that dominated, contradictions, the actual subject. Avoid generic ("a productive session"). Embrace honest framing of drift when the data shows it.
Examples (calibration):
  - eleven minutes of wifi troubleshooting disguised as productivity
  - three hours of looking at apartments you can't afford
  - the matcha question, asked four ways, never answered
  - instagram, with breaks for instagram
  - a slack-shaped tuesday that mostly was hbo max

OPPORTUNITY — one specific activity that takes ABOUT {mins} minutes that the user could have done instead. Lowercase. Under 55 chars. Start with a past-tense verb. Be specific. When DRIFT >> INTENT in the data, lean into the opportunity that contrasts with what they were drifting on (e.g., if they were shopping, suggest something non-consumption; if scrolling, suggest something solitary).
Examples:
  - made an espresso (5 min)
  - called your mom (12 min)
  - read 30 pages (30 min)
  - watched a sitcom (60 min)
  - watched the godfather (180 min)

Session data:
  duration:    {mins} minutes
  drift:       {drift_min} min
  intent:      {intent_min} min
  top sites:   {sites}
  searches:    {searches}
  categories:  {cats}
  apps used:   {apps_text}

Output ONLY the JSON object. No preamble, no markdown fence."""

    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 250,
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
        # Strip optional markdown fence
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:-1])
        parsed = json.loads(text)
        headline = (parsed.get("headline") or "").strip("'\"`. ")
        opportunity = (parsed.get("opportunity") or "").strip("'\"`. ")
        if len(headline) > 70:
            headline = headline[:67].rstrip() + "..."
        if len(opportunity) > 60:
            opportunity = opportunity[:57].rstrip() + "..."
        return {
            "headline": headline or None,
            "opportunity": opportunity or None,
        }
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError,
            json.JSONDecodeError, TimeoutError, ValueError) as e:
        print(f"  [api skipped] {type(e).__name__}: {e}")
        return {"headline": None, "opportunity": None}


# Back-compat alias for the SARAH episode title path
def generate_oneliner(agg: "Aggregated", duration: timedelta,
                      timeout_sec: float = 4.0) -> str | None:
    return generate_receipt_lines(agg, duration, timeout_sec).get("headline")


# ---- aggregation ----

@dataclass
class TopPage:
    url: str
    title: str
    domain: str
    count: int


@dataclass
class AppRecord:
    bundle_id: str
    name: str
    duration_seconds: float
    kind: str   # 'drift' / 'intent' / 'neutral'


@dataclass
class ContactRecord:
    handle: str
    display_name: str
    count: int


@dataclass
class Aggregated:
    duration: timedelta
    visit_count: int
    domain_counts: list[tuple[str, int]]
    top_pages: list[TopPage]
    searches: list[str]
    category_seconds: dict[str, float]
    longest_domain: tuple[str, float] | None
    total_active_seconds: float
    apps: list[AppRecord] = field(default_factory=list)
    contacts: list[ContactRecord] = field(default_factory=list)
    drift_seconds: float = 0.0
    intent_seconds: float = 0.0


def aggregate(session: Session, visits: Sequence[Visit],
              apps: Sequence | None = None,
              contacts: Sequence | None = None) -> Aggregated:
    domain_counter: Counter[str] = Counter()
    domain_duration: Counter[str] = Counter()
    url_counter: Counter[str] = Counter()
    url_titles: dict[str, str] = {}
    url_domains: dict[str, str] = {}
    seen_searches: set[str] = set()
    searches: list[str] = []
    total_active = 0.0
    category_seconds: dict[str, float] = {k: 0.0 for k, _ in CATEGORIES_ORDERED}
    category_seconds["OTHER"] = 0.0

    for v in visits:
        domain_counter[v.domain] += 1
        domain_duration[v.domain] += v.duration_seconds
        total_active += v.duration_seconds
        category_seconds[categorize(v.domain)] += v.duration_seconds
        url_counter[v.url] += 1
        if v.title:
            url_titles[v.url] = v.title
        url_domains[v.url] = v.domain
        q = extract_google_query(v.url)
        if q and q.lower() not in seen_searches:
            seen_searches.add(q.lower())
            searches.append(q)

    longest = None
    if domain_duration:
        d, sec = max(domain_duration.items(), key=lambda kv: kv[1])
        longest = (d, sec)

    # Top pages — dedup by (title, domain) so multiple URLs with the same
    # title roll up into one row. Exclude search domains (they're in SEARCHES).
    SEARCH_DOMAINS = {"google.com", "duckduckgo.com", "bing.com",
                      "scholar.google.com", "ecosia.org"}
    from urllib.parse import urlparse
    title_groups: dict[tuple[str, str], TopPage] = {}
    for url, count in url_counter.items():
        dom = url_domains.get(url, "")
        if dom in SEARCH_DOMAINS:
            continue
        title = (url_titles.get(url) or "").strip()
        if not title:
            try:
                title = urlparse(url).path or "/"
            except ValueError:
                title = url
        key = (title, dom)
        if key in title_groups:
            title_groups[key].count += count
        else:
            title_groups[key] = TopPage(url=url, title=title,
                                         domain=dom, count=count)
    pages = sorted(title_groups.values(), key=lambda p: -p.count)[:10]

    # Build app records with drift/intent kind
    app_records: list[AppRecord] = []
    for a in (apps or []):
        # Filter very short noise (under 5s)
        if a.duration_seconds < 5:
            continue
        app_records.append(AppRecord(
            bundle_id=a.bundle_id, name=a.name,
            duration_seconds=a.duration_seconds,
            kind=app_kind(a.bundle_id),
        ))
    app_records.sort(key=lambda r: -r.duration_seconds)

    contact_records: list[ContactRecord] = []
    for c in (contacts or []):
        contact_records.append(ContactRecord(
            handle=c.handle, display_name=c.display_name, count=c.count,
        ))

    # DRIFT vs INTENT totals — combine web-categories and native-app time.
    # Browsers are neutral apps (NEUTRAL_APPS) so we don't double-count.
    drift_seconds = sum(
        sec for cat, sec in category_seconds.items()
        if web_category_kind(cat) == "drift"
    ) + sum(r.duration_seconds for r in app_records if r.kind == "drift")
    intent_seconds = sum(
        sec for cat, sec in category_seconds.items()
        if web_category_kind(cat) == "intent"
    ) + sum(r.duration_seconds for r in app_records if r.kind == "intent")

    return Aggregated(
        duration=session.duration,
        visit_count=len(visits),
        domain_counts=domain_counter.most_common(),
        top_pages=pages,
        searches=searches,
        category_seconds=category_seconds,
        longest_domain=longest,
        total_active_seconds=total_active,
        apps=app_records,
        contacts=contact_records,
        drift_seconds=drift_seconds,
        intent_seconds=intent_seconds,
    )


# ---- text rendering ----

# Display categories drift-first; AI between since it can swing either way
# but is mostly intent now. SEARCH at the bottom — means-to-an-end.
CATEGORY_ORDER_DISPLAY = ["SOCIAL", "MEDIA", "SHOPPING", "NEWS", "LIFE",
                          "AI", "SCHOOL", "WORK", "EMAIL",
                          "SEARCH", "OTHER"]
# Visual divider between drift and intent halves of the categories list
DRIFT_LAST = "LIFE"  # last drift category in display order


def render_text(session: Session, agg: Aggregated, cfg: dict,
                oneliner: str | None = None,
                opportunity: str | None = None) -> list[str]:
    title = cfg["receipt"].get("title", "TIME SINK")
    subtitle = cfg["receipt"].get("subtitle", "")
    owner = cfg["receipt"].get("owner", "")
    started = session.started_at.astimezone()
    ended = (session.ended_at or datetime.now().astimezone())
    rule = "-" * CHAR_WIDTH
    seed = started.isoformat()

    lines: list[str] = []
    lines.append(_barcode(seed))
    lines.append("")
    lines.append(title.center(CHAR_WIDTH))
    if subtitle:
        lines.append(subtitle.center(CHAR_WIDTH))
    lines.append(rule)
    txn = f"NO. {_transaction_id(seed)}"
    date_str = started.strftime("%Y-%m-%d")
    lines.append(f"  {txn}{date_str.rjust(CHAR_WIDTH - 4 - len(txn))}")
    lines.append(f"  OPENED  {started.strftime('%H:%M:%S')}")
    lines.append(f"  CLOSED  {ended.strftime('%H:%M:%S')}")
    lines.append(rule)

    # Optional one-liner sits as a "headline" of the session
    if oneliner:
        lines.append("")
        lines.append(f'"{oneliner}"'.center(CHAR_WIDTH))

    lines.append("")
    lines.append(f"SESSION DURATION   {_hms(agg.duration.total_seconds()):>{CHAR_WIDTH-19}}")
    lines.append(f"TAB-TIME TOTAL     {_hms(agg.total_active_seconds):>{CHAR_WIDTH-19}}")
    lines.append(f"PAGES LOADED       {agg.visit_count:>{CHAR_WIDTH-19}}")
    lines.append("")

    # DRIFT vs INTENT — the dominant metric. No labels explaining; the
    # contrast does the work.
    if agg.drift_seconds > 0 or agg.intent_seconds > 0:
        lines.append(f"DRIFT              {_hms(agg.drift_seconds):>{CHAR_WIDTH-19}}")
        lines.append(f"INTENT             {_hms(agg.intent_seconds):>{CHAR_WIDTH-19}}")
        lines.append("")

    # APPS USED — non-browser native app time, drift first, then a soft
    # divider, then intent. Browsers excluded (they're already in CATEGORIES).
    drift_apps = [a for a in agg.apps if a.kind == "drift" and a.duration_seconds >= 30]
    intent_apps = [a for a in agg.apps if a.kind == "intent" and a.duration_seconds >= 30]
    if drift_apps or intent_apps:
        lines.append("APPS USED")
        for a in drift_apps[:8]:
            label = f"  {_truncate(a.name, 28)}"
            time_str = _hms(a.duration_seconds)
            lines.append(label + time_str.rjust(CHAR_WIDTH - len(label)))
        if drift_apps and intent_apps:
            lines.append("  " + ("- " * 22))
        for a in intent_apps[:8]:
            label = f"  {_truncate(a.name, 28)}"
            time_str = _hms(a.duration_seconds)
            lines.append(label + time_str.rjust(CHAR_WIDTH - len(label)))
        lines.append("")

    # Categories block — drift first, then a soft divider, then intent.
    cats_to_show = [
        (k, agg.category_seconds.get(k, 0.0))
        for k in CATEGORY_ORDER_DISPLAY
        if agg.category_seconds.get(k, 0.0) > 30
    ]
    if cats_to_show:
        lines.append("CATEGORIES")
        # Inject a divider between drift and intent halves
        last_kind = None
        for k, sec in cats_to_show:
            kind = web_category_kind(k)
            if last_kind == "drift" and kind != "drift":
                lines.append("  " + ("- " * 22))
            last_kind = kind
            label = f"  {k}"
            time_str = _hms(sec)
            lines.append(label + time_str.rjust(CHAR_WIDTH - len(label)))
        lines.append("")

    # Top pages — title + domain, with visit count
    if agg.top_pages:
        lines.append("TOP PAGES")
        for p in agg.top_pages[:8]:
            count_str = f"x{p.count}"
            domain_str = f"  {p.domain}"
            domain_line = domain_str + count_str.rjust(CHAR_WIDTH - len(domain_str))
            title_line = "  " + _truncate(p.title, CHAR_WIDTH - 2)
            lines.append(title_line)
            lines.append(domain_line)
        lines.append("")

    if agg.searches:
        lines.append(f"SEARCHES ({len(agg.searches)})")
        for q in agg.searches[:8]:
            lines.append("  " + _truncate(q, CHAR_WIDTH - 2))
        if len(agg.searches) > 8:
            lines.append(f"  + {len(agg.searches) - 8} more")
        lines.append("")

    # TEXTED — outgoing messages by contact. Names from config; otherwise
    # anonymized in contacts.py to last-4-digits format.
    if agg.contacts:
        lines.append("TEXTED")
        for c in agg.contacts[:8]:
            label = f"  {_truncate(c.display_name, 32)}"
            count_str = f"x{c.count}"
            lines.append(label + count_str.rjust(CHAR_WIDTH - len(label)))
        if len(agg.contacts) > 8:
            lines.append(f"  + {len(agg.contacts) - 8} others")
        lines.append("")

    if agg.longest_domain:
        d, sec = agg.longest_domain
        lines.append("BIGGEST TIME SINK")
        lines.append(f"  {_truncate(d, CHAR_WIDTH - 2)}")
        lines.append(f"  {_hms(sec):>{CHAR_WIDTH - 2}}")
        lines.append("")

    lines.append(rule)
    lines.append("")

    # Opportunity cost footer — AI-generated if available, else static list
    activity = opportunity or opportunity_cost(agg.duration, seed=seed)
    dur_str = _hms(agg.duration.total_seconds())
    if activity:
        lines.append(f"IN {dur_str} YOU COULD HAVE".center(CHAR_WIDTH))
        # Activity may be long; wrap if needed
        words = activity.split()
        cur = ""
        wrapped_lines = []
        for w in words:
            test = (cur + " " + w).strip()
            if len(test) > CHAR_WIDTH - 4:
                wrapped_lines.append(cur)
                cur = w
            else:
                cur = test
        if cur:
            wrapped_lines.append(cur)
        for wl in wrapped_lines:
            lines.append(wl.upper().center(CHAR_WIDTH))
    lines.append("")

    if owner:
        lines.append(owner.center(CHAR_WIDTH))
        lines.append("")
    lines.append(_barcode(seed[::-1]))
    lines.append("")
    # Sanitize all output to ASCII-friendly chars before printing
    return [_sanitize(line) for line in lines]


# ---- PNG rendering (preview) ----

def render_png(lines: list[str], out_path: Path,
               bg: str = "white", fg: str = "black") -> Path:
    font = _load_font(FONT_SIZE)
    img_height = MARGIN_PX * 2 + LINE_HEIGHT_PX * len(lines)
    img = Image.new("RGB", (PAPER_WIDTH_PX, img_height), bg)
    draw = ImageDraw.Draw(img)
    y = MARGIN_PX
    for line in lines:
        draw.text((MARGIN_PX, y), line, font=font, fill=fg)
        y += LINE_HEIGHT_PX
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
