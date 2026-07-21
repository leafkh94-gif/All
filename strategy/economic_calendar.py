"""
strategy/economic_calendar.py
-------------------------------
Real (predictive) economic calendar -- a forward-looking upgrade to the
reactive headline feed in strategy/news_calendar.py. That module only reacts
*after* a matching headline is published; this one knows a scheduled
event's time *before* it happens (FOMC/CPI/NFP/rate decisions/etc.), so new
alerts can be paused ahead of a known catalyst, not just after.

Source: Forex Factory's public "this week" calendar XML feed. No signup, no
API key -- same no-friction choice already made for news_calendar.py, since
every paid calendar API (Finnhub, financialmodelingprep, TradingEconomics)
needs a signed-up key and this one doesn't.

NOTE: I could not verify the exact feed URL or its timestamp timezone from
this sandbox (no live network access to non-GitHub hosts here) -- Forex
Factory's calendar feed is widely used by MT4/MT5 expert advisors at the URL
below, and is documented (by convention, not by anything I could confirm
live) to publish times in US Eastern. Both the URL and the assumed timezone
are env-overridable for exactly this reason. Fails open on any error (bad
URL, timeout, malformed XML, unparseable date/time) -- always returns []
rather than ever blocking a scan.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

import strategy_config as cfg

ECONOMIC_CALENDAR_URL = os.environ.get(
    "ECONOMIC_CALENDAR_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.xml")
ECONOMIC_CALENDAR_TZ = os.environ.get("ECONOMIC_CALENDAR_TZ", "America/New_York")

CACHE_DIR = ".cache"
CACHE_PATH = os.path.join(CACHE_DIR, "economic_calendar.json")
CACHE_TTL_SECONDS = 30 * 60  # a weekly calendar doesn't change minute to minute

_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*(am|pm)$", re.IGNORECASE)


def _feed_tzinfo():
    try:
        return ZoneInfo(ECONOMIC_CALENDAR_TZ)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=-5))  # US Eastern standard time, defensive fallback


def _parse_event_datetime(date_str, time_str):
    """('MM-DD-YYYY', '8:30am'|'All Day'|'Tentative'|...) -> aware UTC
    datetime, or None if the time isn't a concrete clock time. Never raises."""
    if not date_str or not time_str:
        return None
    m = _TIME_RE.match(time_str.strip())
    if not m:
        return None  # "All Day" / "Tentative" / etc. -- no specific instant to guard around
    try:
        month, day, year = (int(p) for p in date_str.strip().split("-"))
        hour = int(m.group(1)) % 12
        minute = int(m.group(2))
        if m.group(3).lower() == "pm":
            hour += 12
        local_dt = datetime(year, month, day, hour, minute, tzinfo=_feed_tzinfo())
        return local_dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _read_cache():
    try:
        if time.time() - os.path.getmtime(CACHE_PATH) < CACHE_TTL_SECONDS:
            with open(CACHE_PATH) as f:
                return json.load(f)
    except (FileNotFoundError, OSError, ValueError):
        pass
    return None


def _write_cache(events):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(events, f)
    except OSError:
        pass


def fetch_upcoming_events(now_utc):
    """Best-effort fetch of this week's calendar events. Never raises."""
    try:
        cached = _read_cache()
        if cached is not None:
            return cached
        r = requests.get(ECONOMIC_CALENDAR_URL, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ElementTree.fromstring(r.content)
        events = []
        for item in root.iter("event"):
            title = item.findtext("title")
            country = item.findtext("country")
            impact = item.findtext("impact")
            event_dt = _parse_event_datetime(item.findtext("date"), item.findtext("time"))
            if not title or event_dt is None:
                continue
            events.append({"title": title, "country": country, "impact": impact,
                            "time": event_dt.isoformat()})
        _write_cache(events)
        return events
    except Exception:
        return []


def is_economic_blackout_active(now_utc, events, before_minutes=None, after_minutes=None,
                                 min_impact=None, relevant_currencies=None):
    """(active: bool, title: str|None) for the nearest relevant high-impact
    event whose [before, after] window contains now_utc. Never raises on
    malformed event data."""
    before_minutes = cfg.ECON_BLACKOUT_MINUTES_BEFORE if before_minutes is None else before_minutes
    after_minutes = cfg.ECON_BLACKOUT_MINUTES_AFTER if after_minutes is None else after_minutes
    min_impact = cfg.ECON_CALENDAR_MIN_IMPACT if min_impact is None else min_impact
    relevant_currencies = cfg.ECON_CALENDAR_RELEVANT_CURRENCIES if relevant_currencies is None \
        else relevant_currencies

    for e in events or []:
        try:
            if (e.get("impact") or "").strip().lower() != min_impact.strip().lower():
                continue
            if (e.get("country") or "").strip().upper() not in relevant_currencies:
                continue
            event_time = e.get("time")
            if isinstance(event_time, str):
                event_time = datetime.fromisoformat(event_time)
            if event_time is None:
                continue
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            window_start = event_time - timedelta(minutes=before_minutes)
            window_end = event_time + timedelta(minutes=after_minutes)
            if window_start <= now_utc <= window_end:
                return True, e.get("title")
        except Exception:
            continue
    return False, None
