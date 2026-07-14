"""
strategy/news_calendar.py
--------------------------
Reactive market-news awareness via a public RSS headline feed (default:
Khaleej Times) -- zero signup, no API key. Capital.com has no news endpoint
(see strategy/capital_feed.py -- only /session, /markets, /prices/{epic}),
and a real forward-looking economic calendar needs a paid/signup API,
which the user opted out of in favor of this no-friction path.

This trades prediction for reach: instead of warning *before* a scheduled
event (FOMC/CPI/NFP), it reacts *after* a matching headline is published,
using keyword matching against the tracked instruments' macro drivers.

NOTE: I could not verify the exact RSS feed URL from this sandbox --
khaleejtimes.com (and several other news sites/aggregators, including
Reuters and Google News) returned 403 to every fetch attempt here, which
looks like bot-defense on their end rather than a general network failure
(a plain GitHub raw fetch worked fine in the same session). NEWS_RSS_URL
is fully overridable via env var for exactly this reason -- if the
default 403s/404s in production, set the correct URL there, no code
change needed. Fails open on any error (bad URL, timeout, malformed XML)
-- always returns [] rather than ever blocking a scan.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import requests

import strategy_config as cfg

NEWS_RSS_URL = os.environ.get("NEWS_RSS_URL", "https://www.khaleejtimes.com/rss")

CACHE_DIR = ".cache"
CACHE_PATH = os.path.join(CACHE_DIR, "news_rss.json")
CACHE_TTL_SECONDS = 10 * 60   # headlines move faster than a calendar; check more often

# Keyword match against the headline title decides relevance to
# US500/US100/US30/BTCUSD -- a general UAE/Gulf news feed is mostly noise
# otherwise. Deliberately broad; false positives just cost a quiet period,
# false negatives cost nothing extra beyond today's baseline.
RELEVANT_KEYWORDS = [
    "fed", "federal reserve", "interest rate", "rate decision", "rate cut", "rate hike",
    "inflation", "cpi", "nfp", "non-farm payroll", "jobs report", "unemployment",
    "fomc", "powell", "recession", "gdp", "s&p", "nasdaq", "dow jones", "wall street",
    "bitcoin", "crypto", "btc", "stock market",
]


def _read_cache():
    try:
        if time.time() - os.path.getmtime(CACHE_PATH) < CACHE_TTL_SECONDS:
            with open(CACHE_PATH) as f:
                return json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        pass
    return None


def _write_cache(headlines):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(headlines, f)
    except OSError:
        pass


def fetch_recent_headlines(now_utc):
    """Best-effort fetch of recent RSS headlines. Never raises."""
    try:
        cached = _read_cache()
        if cached is not None:
            return cached
        r = requests.get(NEWS_RSS_URL, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ElementTree.fromstring(r.content)
        headlines = []
        for item in root.iter("item"):
            title = item.findtext("title")
            pub_date_raw = item.findtext("pubDate")
            if not title or not pub_date_raw:
                continue
            try:
                pub_date = parsedate_to_datetime(pub_date_raw)
            except (TypeError, ValueError):
                continue
            if pub_date.tzinfo is None:
                pub_date = pub_date.replace(tzinfo=timezone.utc)
            headlines.append({"title": title, "time": pub_date.isoformat()})
        _write_cache(headlines)
        return headlines
    except Exception:
        return []


def is_news_blackout_active(now_utc, headlines, after_minutes=None, keywords=None):
    """(active: bool, headline: str|None) for the most recent relevant
    headline still within its blackout window. Never raises on malformed
    headline data."""
    after_minutes = cfg.NEWS_BLACKOUT_MINUTES_AFTER if after_minutes is None else after_minutes
    keywords = keywords or RELEVANT_KEYWORDS

    for h in headlines or []:
        try:
            title = h.get("title") or ""
            if not any(kw in title.lower() for kw in keywords):
                continue
            pub_time = h.get("time")
            if isinstance(pub_time, str):
                pub_time = datetime.fromisoformat(pub_time)
            if pub_time is None:
                continue
            if pub_time.tzinfo is None:
                pub_time = pub_time.replace(tzinfo=timezone.utc)
            if now_utc < pub_time:
                continue  # clock-skew guard -- a headline can't be from the future
            if now_utc - pub_time <= timedelta(minutes=after_minutes):
                return True, title
        except Exception:
            continue
    return False, None
