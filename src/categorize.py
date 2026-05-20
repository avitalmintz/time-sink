"""Map a domain to a category for the receipt's CATEGORIES block.

Match order matters: more specific buckets first. If nothing matches, OTHER.
"""
from __future__ import annotations

CATEGORIES_ORDERED: list[tuple[str, list[str]]] = [
    ("SCHOOL", [
        "uchicago.edu", "canvas.uchicago.edu", "uchicago.co1.qualtrics.com",
        "my.uchicago.edu", "portal.uchicago.edu", "ais.uchicago.edu",
        "okta.com", "duosecurity.com", "shibboleth2.uchicago.edu",
        "urldefense.com",
        "scholar.google.com",
        "qualtrics.com",
    ]),
    ("AI", [
        "chatgpt.com", "chat.openai.com", "claude.ai", "openai.com",
        "anthropic.com", "gemini.google.com", "perplexity.ai", "poe.com",
    ]),
    ("EMAIL", [
        "mail.google.com", "outlook.com", "outlook.office.com",
        "office.com", "icloud.com",
    ]),
    ("SOCIAL", [
        "instagram.com", "twitter.com", "x.com", "tiktok.com", "facebook.com",
        "linkedin.com", "reddit.com", "discord.com", "snapchat.com",
        "bsky.app", "threads.net", "pinterest.com", "tumblr.com",
        "partiful.com",
    ]),
    ("MEDIA", [
        "youtube.com", "netflix.com", "hbomax.com", "play.hbomax.com",
        "spotify.com", "open.spotify.com", "soundcloud.com", "twitch.tv",
        "vimeo.com", "hulu.com", "disneyplus.com", "max.com", "peacocktv.com",
        "primevideo.com",
    ]),
    ("SHOPPING", [
        "amazon.com", "etsy.com", "target.com", "ebay.com", "kayak.com",
        "expedia.com", "streeteasy.com", "zillow.com", "shop.app",
        "depop.com", "thredup.com", "redfin.com", "trivago.com",
        "casadecampo.com.do",
        "sezzle.com", "klarna.com", "afterpay.com", "affirm.com",
    ]),
    ("NEWS", [
        "nytimes.com", "washingtonpost.com", "theatlantic.com", "foxnews.com",
        "cnn.com", "bbc.com", "bbc.co.uk", "vox.com", "buzzfeed.com",
        "wsj.com", "nbcnews.com", "apnews.com", "axios.com", "salon.com",
        "nypost.com", "nymag.com", "newyorker.com", "thecut.com",
    ]),
    ("LIFE", [
        # vet / pet care
        "petdesk.com", "petsmart.com", "chewy.com",
        # banking / financial
        "chase.com", "bankofamerica.com", "wellsfargo.com", "fidelity.com",
        "vanguard.com", "schwab.com", "ally.com", "venmo.com", "paypal.com",
        # healthcare
        "mychart.com", "onemedical.com", "labcorp.com", "questdiagnostics.com",
        # food delivery
        "doordash.com", "ubereats.com", "grubhub.com", "seamless.com",
        # transportation
        "uber.com", "lyft.com",
        # other admin
        "homefromcollege.com",
    ]),
    ("WORK", [
        "docs.google.com", "drive.google.com", "github.com", "slack.com",
        "notion.so", "linear.app", "atlassian.net", "jira.com",
        "render.com", "dashboard.render.com", "vercel.com",
        "127.0.0.1", "localhost",
        "cal.com", "calendly.com", "zoom.us",
        "guidebook.com",
        "figma.com", "miro.com",
    ]),
    ("SEARCH", [
        "google.com", "duckduckgo.com", "bing.com", "ecosia.org",
    ]),
]


def categorize(domain: str | None) -> str:
    if not domain:
        return "OTHER"
    d = domain.lower()
    for label, patterns in CATEGORIES_ORDERED:
        for p in patterns:
            # Exact match or subdomain match
            if d == p or d.endswith("." + p):
                return label
    return "OTHER"
