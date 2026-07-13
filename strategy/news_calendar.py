"""
strategy/news_calendar.py
-------------------------
Real economic-calendar awareness (FOMC/CPI/NFP-style high-impact events).
Capital.com has no news/calendar endpoint (see strategy/capital_feed.py --
only /session, /markets, /prices/{epic}), so this hits a separate provider.

Defaults to Finnhub's economic-calendar endpoint -- NOTE: this could not be
verified against a live API from this sandbox (no network access), so if the
free tier doesn't include this endpoint, swap NEWS_CALENDAR_BASE_URL to a
comparable provider (e.g. financialmodelingprep.com's economic_calendar).
Nothing else here hardcodes the provider's shape beyond a list of
{"event", "time", "impact", "country"} dicts.

Fails open on any error (network, auth, unexpected shape): returns [] rather
than ever blocking a scan, matching the rest of the codebase's defensive
conventions (see strategy/scan_diagnostics.py, main_alerts.run()'s
per-instrument try/except).
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

import strategy_config as cfg

NEWS_CALENDAR_BASE_URL = os.environ.get(
    "NEWS_CALENDAR_BASE_URL", "https://finnhub.io/api/v1/calendar/economic")

CACHE_DIR = ".cache"
CACHE_PATH = os.path.join(CACHE_DIR, "news_calendar.json")
CACHE_TTL_SECONDS = 30 * 60   # events don't change minute to minute; be free-tier friendly

IMPACT_RANK = {"low": 0, "medium": 1, "high": 2}


def _read_cache():
    try:
        if time.time() - os.path.getmtime(CACHE_PATH) < CACHE_TTL_SECONDS:
            with open(CACHE_PATH) as f:
                return json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return None


def _write_cache(events):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(events, f)
    except OSError:
        pass


def fetch_high_impact_events(now_utc):
    """Best-effort fetch of nearby economic events. Never raises."""
    try:
        cached = _read_cache()
        if cached is not None:
            return cached
        api_key = os.environ.get("NEWS_CALENDAR_API_KEY")
        if not api_key:
            return []
        params = {
            "from": (now_utc - timedelta(hours=1)).strftime("%Y-%m-%d"),
            "to": (now_utc + timedelta(hours=6)).strftime("%Y-%m-%d"),
            "token": api_key,
        }
        r = requests.get(NEWS_CALENDAR_BASE_URL, params=params, timeout=10)
        r.raise_for_status()
        raw = r.json().get("economicCalendar", [])
        events = [
            {"event": e.get("event"), "time": e.get("time") or e.get("date"),
             "impact": e.get("impact"), "country": e.get("country")}
            for e in raw
        ]
        _write_cache(events)
        return events
    except Exception:
        return []


def is_news_blackout_active(now_utc, events, before_minutes=None, after_minutes=None, min_impact=None):
    """(active: bool, event_name: str|None) for the first matching high-impact
    event within its blackout window. Never raises on malformed event data."""
    before_minutes = cfg.NEWS_BLACKOUT_MINUTES_BEFORE if before_minutes is None else before_minutes
    after_minutes = cfg.NEWS_BLACKOUT_MINUTES_AFTER if after_minutes is None else after_minutes
    min_impact = min_impact or cfg.NEWS_CALENDAR_MIN_IMPACT
    min_rank = IMPACT_RANK.get(min_impact.lower(), 2)

    for e in events or []:
        try:
            impact = str(e.get("impact", "")).lower()
            if IMPACT_RANK.get(impact, -1) < min_rank:
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
                return True, e.get("event")
        except Exception:
            continue
    return False, None
