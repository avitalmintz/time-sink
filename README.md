# TIME SINK

**A student art project — made for a media studies class on conscious media practices.**
Not a product. Not production code. A piece I made for a course.

---

## What it is

TIME SINK is a small system that watches its own user (me) browse the internet, then — every time I close my laptop — prints a paper receipt summarizing what I just did.

Each receipt:
- Lists the sites I visited, the actual searches I made, and the longest single thing I looked at
- Has an AI-generated headline that summarizes the session in one line
- Has an AI-generated "opportunity cost" line — what I could have done with that time instead ("called your mom," "watched a movie")
- Includes a real scannable QR code at the bottom

When you scan the QR code, you go to a public webpage that:
1. Shows the receipt content again, styled to look like a paper receipt
2. Shows a 150-200 word AI essay roasting that specific session, themed around the class's topic (conscious consumption, attention economics, "brainrot")
3. Has a link to "See the wall" — every receipt I've generated, tiled together like an installation

The receipts are also collected on a public wall page. Each receipt is a small artifact; the collection is the portrait.

A second mode triggers a separate receipt every time I text the same friend ten times.

The argument of the piece, in one sentence: **what you scroll past becomes evidence, becomes critique, becomes paper.**

---

## What's in this repo

```
time-sink/
├── src/
│   ├── readers.py        — read Chrome history + clipboard activity
│   ├── sessions.py       — sleep/wake tracking, session boundaries
│   ├── categorize.py     — group domains into categories (SOCIAL, SCHOOL, ...)
│   ├── receipt.py        — render a session receipt (text + PNG preview)
│   ├── episode_receipt.py — render a SARAH-mode receipt (text-friend trigger)
│   ├── sarah.py          — read Messages app database, batch outgoing texts
│   ├── printer.py        — send ESC/POS to a thermal printer over TCP, with a queue for offline
│   ├── publish.py        — POST receipt data to the public log server
│   ├── tracking_flag.py  — on/off file flag (toggled by the menu bar app)
│   └── config.py
├── scripts/
│   ├── menubar.py            — macOS menu bar app to toggle tracking
│   ├── print_session.py      — main entry point: sleep / wake / now / sarah
│   ├── preview_receipt.py    — generate a PNG preview without printing
│   ├── backfill_wall.py      — replay past sessions onto the public wall
│   ├── on_sleep.sh / on_wake.sh — sleepwatcher hooks
│   ├── start_menubar.sh
│   ├── install_menubar_autostart.sh
│   └── com.avitalmintz.timesink.menubar.plist — LaunchAgent
├── sleephooks/install.sh    — installs the sleepwatcher hooks
└── config.example.json      — template config (real values go in config.json, gitignored)
```

The companion repo is [time-sink-log](https://github.com/avitalmintz/time-sink-log) — the small Flask server that hosts the public-facing wall and the QR-code roast pages.

---

## How the pieces fit together

```
   [me using my laptop]
            │
   close lid (real macOS sleep event)
            │
            ▼
   sleepwatcher → on_sleep.sh → print_session.py sleep
            │
            ▼
   read Chrome history for this session window
            │
            ▼
   Claude API: one headline + one opportunity-cost line
            │
            ▼
   render receipt text (48-char monospace)
            │
            ├──→ POST to time-sink-log server (Render) → returns short URL
            │
            ▼
   send ESC/POS bytes to the NETUM thermal printer over TCP:9100
   (or queue locally if printer is unreachable; flush on next wake)
            │
            ▼
   paper comes out with a real QR code
```

When someone scans the QR code, they hit `time-sink-log.onrender.com/r/<id>`, which generates a personalized essay using Claude and serves an HTML page. From there they can navigate to `/` to see every receipt ever generated, tiled like a wall mural.

---

## What you'd need to run this yourself

This isn't a polished app to install — it's a personal project to look at. But if you wanted to:

- macOS (uses Apple's Messages database, Chrome's history database, and Mac's sleepwatcher)
- A NETUM 80mm WiFi thermal receipt printer (or any 80mm ESC/POS network printer)
- `brew install sleepwatcher`
- Python 3 + the packages in `requirements.txt`
- An [Anthropic API key](https://console.anthropic.com) (~$0.002 per receipt, for the AI lines)
- Full Disk Access granted to your Terminal AND to your Python binary (so the hooks can read Messages)
- Copy `config.example.json` → `config.json` and fill in your printer IP, friend's phone, etc.
- Run `scripts/sleephooks/install.sh` and `scripts/install_menubar_autostart.sh`

The companion server lives in a separate repo, deploys to Render's free tier, and only needs Flask. See [time-sink-log](https://github.com/avitalmintz/time-sink-log).

---

## About the privacy choices

This project intentionally exposes things about its maker (me) on a public webpage. But:

- **Only outgoing messages** are read by Claude for the SARAH-mode receipts. My friend's words stay local.
- **`config.json` is gitignored** so phone numbers, names, and printer IPs aren't in the public repo.
- **Anthropic doesn't train on API content** (per their terms).
- **Search content is shown verbatim on receipts.** That's deliberate. The point of the piece is to confront the gap between what I think I do and what I actually do; sanitized data wouldn't.

The redaction layer (`redaction` block in `config.json`) exists for keywords or domains I want to skip — medical sites, friend names, etc. — but is intentionally minimal by default.

---

## Class context

This is a class project. The intended audience is one class. The repo is public for portfolio/documentation purposes, not because the system is meant to be deployed by anyone else. It is unpolished by design.
