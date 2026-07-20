"""
Trading Alert Bot — ICT Killzone session scoring, plus session-range liquidity
levels (Entry/SL/TP Selection Rules v1.3) used for TP2/TP3 construction.
All times UTC.
"""
from datetime import datetime, time, timezone

# (name, start, end, bonus_us_index, bonus_btc) — end is exclusive, times are UTC.
KILLZONES = [
    ("ASIAN_SESSION",        time(0, 0),  time(6, 0),  2,  3),
    ("LONDON_PRE_KILL",      time(6, 0),  time(7, 0),  6,  4),
    ("LONDON_KILLZONE",      time(7, 0),  time(8, 30), 12, 6),
    ("NY_PRE_MARKET",        time(11, 30), time(12, 30), 6, 6),
    ("NY_KILLZONE",          time(12, 30), time(14, 0), 12, 10),
]

DEAD_ZONE_PENALTY_US_INDEX = -4
DEAD_ZONE_PENALTY_BTC = -2


def killzone_bonus(now_utc, instrument_class):
    """Return (bonus_points, zone_name) for the given UTC time and instrument class
    ('US_INDEX' or 'CRYPTO'). Falls back to the dead-zone penalty outside all windows."""
    t = now_utc.time()
    for name, start, end, bonus_index, bonus_btc in KILLZONES:
        if start <= t < end:
            return (bonus_index, name) if instrument_class == "US_INDEX" else (bonus_btc, name)
    penalty = DEAD_ZONE_PENALTY_US_INDEX if instrument_class == "US_INDEX" else DEAD_ZONE_PENALTY_BTC
    return penalty, "DEAD_ZONE"


# ─────────────────────────────────────────────────────────────────────
# Session-range liquidity levels (Entry/SL/TP Selection Rules v1.3, §TP2/TP3)
# Distinct from the KILLZONES table above (a scoring bonus) -- these are
# broader session windows used purely to compute a high/low range for TP
# target selection, not to score anything.
# ─────────────────────────────────────────────────────────────────────
ASIAN_SESSION = (time(0, 0), time(6, 0))
LONDON_SESSION = (time(7, 0), time(16, 0))
NY_SESSION = (time(12, 0), time(21, 0))


def _parsed_candle_times(candles):
    """Yield (datetime, candle) for every candle with a parseable "t" field.
    Never raises -- a candle with a missing/malformed timestamp is skipped."""
    for c in candles:
        raw = c.get("t") if isinstance(c, dict) else None
        if raw is None:
            continue
        try:
            t = datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        yield t, c


def session_range(candles, now_utc, start_time, end_time):
    """High/low of the most recently represented UTC-calendar-day occurrence
    of the [start_time, end_time) session window within `candles` (up to and
    including `now_utc` -- an in-progress session returns its partial range
    so far). Returns (None, None) if no candle in `candles` falls in that
    window at all (e.g. the fetched history doesn't reach back far enough)."""
    matches = []
    for t, c in _parsed_candle_times(candles):
        if t > now_utc:
            continue
        if not (start_time <= t.time() < end_time):
            continue
        matches.append((t.date(), c))
    if not matches:
        return None, None
    most_recent_day = max(day for day, _ in matches)
    day_candles = [c for day, c in matches if day == most_recent_day]
    return float(max(c["h"] for c in day_candles)), float(min(c["l"] for c in day_candles))


def daily_open(candles, now_utc):
    """Open price of the earliest candle in `candles` whose UTC calendar date
    matches now_utc's, or None if today's opening candle isn't in the fetched
    history (e.g. too early in the day for the lookback window to reach it)."""
    today = now_utc.date()
    todays = [(t, c) for t, c in _parsed_candle_times(candles) if t.date() == today and t <= now_utc]
    if not todays:
        return None
    todays.sort(key=lambda pair: pair[0])
    return float(todays[0][1]["o"])


def weekly_open(candles, now_utc):
    """Open price of the earliest candle in `candles` whose ISO (year, week)
    matches now_utc's, or None if this week's opening candle isn't in the
    fetched history."""
    this_week = now_utc.isocalendar()[:2]
    matches = [(t, c) for t, c in _parsed_candle_times(candles)
               if t.isocalendar()[:2] == this_week and t <= now_utc]
    if not matches:
        return None
    matches.sort(key=lambda pair: pair[0])
    return float(matches[0][1]["o"])
