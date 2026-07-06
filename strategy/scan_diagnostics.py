"""
scan_diagnostics.py
-------------------
Distinguish WHY the scanner reports "no pattern detected":

  1) DATA problem   -> too few, or stale, candles, so pivots/patterns
                       literally cannot form. No tolerance change helps this.
  2) TOLERANCE prob -> enough fresh data, but detectors are too tight and
                       rejected every near-miss candidate.

MIN_BARS_NEEDED is the max of the 5 real pattern detectors' own minimums in
scoring_strategy.py (detect_liquidity_sweep_bos >=23, detect_sd_rejection
>=20, detect_head_shoulders >=30, detect_flag >=16, detect_news_retest >=17)
-- head_shoulders' lookback=30 is the strictest, so that's the bar the
scanner needs to clear for every detector to at least have a chance to run.
"""

from datetime import datetime, timezone

MIN_BARS_NEEDED = 30

# An M15 candle older than this many minutes means the feed is stale for
# this instrument (common on indices at a session open, e.g. Monday).
STALE_AFTER_MIN = 45


def _bar_count(data):
    """Works for pandas DataFrame, list, or None. Never raises."""
    if data is None:
        return 0
    try:
        return len(data)
    except TypeError:
        return 0


def _last_timestamp(data):
    """Best-effort newest-candle time. Returns a datetime or None. Never raises."""
    # pandas DataFrame / Series with a DatetimeIndex
    try:
        idx = getattr(data, "index", None)
        if idx is not None and len(idx) > 0:
            ts = idx[-1]
            return ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
    except Exception:
        pass
    # list of candles (dicts or objects) — Capital.com candles store the
    # raw snapshotTime string under "t" (see strategy/capital_feed.py)
    try:
        last = data[-1]
        value = None
        for key in ("timestamp", "time", "datetime", "date", "t"):
            if isinstance(last, dict) and key in last:
                value = last[key]
                break
            if hasattr(last, key):
                value = getattr(last, key)
                break
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return value
    except Exception:
        pass
    return None


def _minutes_old(ts, now_utc):
    """Age of the last candle in minutes, or None if it can't be computed."""
    if ts is None or now_utc is None:
        return None
    try:
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if getattr(now_utc, "tzinfo", None) is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        return (now_utc - ts).total_seconds() / 60.0
    except Exception:
        return None


def bars_report(symbol, data, now_utc=None):
    """
    Return a one-line diagnostic string for this instrument.
    Call right before pattern detection, after fetching the entry candles.
    """
    n = _bar_count(data)

    if n == 0:
        return (f"{symbol}: 0 bars — feed returned nothing (data problem, not patterns)")

    if n < MIN_BARS_NEEDED:
        short_by = MIN_BARS_NEEDED - n
        return (f"{symbol}: {n}/{MIN_BARS_NEEDED} bars, short by {short_by} "
                f"— data problem, pivots can't form")

    ts = _last_timestamp(data)
    age = _minutes_old(ts, now_utc)

    if age is not None and age > STALE_AFTER_MIN:
        return (f"{symbol}: {n} bars OK but last candle {age:.0f} min old "
                f"— stale feed (data problem, common on indices Mon/session open)")

    age_txt = f", last candle age={age:.0f}min" if age is not None else ""
    return (f"{symbol}: {n} bars OK{age_txt}, fresh — detectors too tight")
