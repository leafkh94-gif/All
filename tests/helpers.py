"""Synthetic candle generators shared across tests."""
from datetime import datetime, timedelta, timezone


def make_candles(n, start_price=100.0, step=0.0, noise=0.0, start_time=None, interval_minutes=15):
    """Generate n simple OHLC candles. `step` moves the close each bar (trend);
    `noise` adds a small deterministic zig-zag so highs/lows differ from o/c."""
    start_time = start_time or datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = start_price
    for i in range(n):
        o = price
        c = o + step + (noise if i % 2 == 0 else -noise)
        h = max(o, c) + abs(noise) * 0.5
        l = min(o, c) - abs(noise) * 0.5
        t = (start_time + timedelta(minutes=interval_minutes * i)).isoformat()
        candles.append({"t": t, "o": round(o, 4), "h": round(h, 4), "l": round(l, 4), "c": round(c, 4), "v": None})
        price = c
    return candles


def trending_h4_candles(n=260, up=True):
    step = 5.0 if up else -5.0
    return make_candles(n, start_price=5000.0, step=step, noise=1.0, interval_minutes=240)
