"""
Gold-only trading alert bot — indicators + round-number level utility.
Golden Trio consumes SMA/ZLSMA/Donchian/RSI/ATR from here; LevelStore still
records PDH/PDL and weekly levels for daily/weekly digests.

PERF: Indicators now accept pre-built DataFrames to avoid redundant reconstruction.
"""
import json
import os

import pandas as pd

import strategy_config as cfg
from strategy import modes

STATE_DIR = "state"
LEVELS_PATH = os.path.join(STATE_DIR, "levels.json")


def candles_to_df(candles):
    """Convert candle list to DataFrame. Caller should cache and reuse this."""
    return pd.DataFrame(candles)


# ────────────────────────────────────────────────────────────────██[...]
# Core indicators
# ────────────────────────────────────────────────────────────────██[...]
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def sma(series, period):
    return series.rolling(period).mean()


def zero_lag_sma(series, period):
    # ZLSMA = 2 * SMA(n) - SMA(SMA(n), n) -- classic zero-lag correction that
    # removes half the SMA's phase delay by subtracting an SMA of the SMA.
    s = sma(series, period)
    return 2 * s - sma(s, period)


def donchian_channels(df, period=20):
    """Turtle Trade Channel: rolling highest-high / lowest-low bands.

    Returned bands include the current bar so `upper.iloc[-1]` is the highest
    high of the last `period` bars (i.e. the level price is testing right
    now), which is what the Golden Trio entry-side proximity check needs."""
    upper = df["h"].rolling(period).max()
    lower = df["l"].rolling(period).min()
    mid = (upper + lower) / 2
    return upper, lower, mid


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def atr(df, period=14):
    prev_close = df["c"].shift(1)
    tr = pd.concat(
        [df["h"] - df["l"], (df["h"] - prev_close).abs(), (df["l"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def atr_percentile(df, lookback=cfg.ATR_LOOKBACK_BARS, period=14):
    """Percentile rank (0-100) of the latest ATR value among the last `lookback` bars."""
    a = atr(df, period)
    window = a.tail(lookback)
    if len(window) < 2:
        return 50.0
    latest = window.iloc[-1]
    return float((window <= latest).sum() / len(window) * 100)


def atr_sweet_spot_penalty(df, lookback=cfg.ATR_LOOKBACK_BARS, period=14, mode=None):
    """Penalize dead (< low pct) and chaotic (> high pct) ATR regimes."""
    m = mode or modes.STANDARD
    pct = atr_percentile(df, lookback, period)
    if pct < m.atr_low_percentile:
        return cfg.ATR_DEAD_MARKET_PENALTY, "dead_market"
    if pct > m.atr_high_percentile:
        return cfg.ATR_TOO_VOLATILE_PENALTY, "too_volatile"
    return 0, "normal"


# ────────────────────────────────────────────────────────────────██[...]
# Round-number proximity (gold: 50-point steps, 3-point proximity)
# ────────────────────────────────────────────────────────────────██[...]
def round_number_bonus(price, instrument):
    entry = cfg.ROUND_NUMBER_OFFSET_TABLE.get(instrument)
    if not entry or price <= 0:
        return 0
    step, proximity = entry
    nearest = round(price / step) * step
    if nearest == 0:
        return 0
    if abs(price - nearest) <= proximity:
        return cfg.ROUND_NUMBER_BONUS
    return 0


# ────────────────────────────────────────────────────────────────██[...]
# Level persistence — PDH/PDL and weekly high/low (kept for digests)
# ────────────────────────────────────────────────────────────────██[...]
class LevelStore:
    def __init__(self, path=LEVELS_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._data = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def set_daily_levels(self, instrument, high, low, day_key):
        self._data.setdefault(instrument, {})["daily"] = {
            "high": high, "low": low, "day_key": day_key,
        }
        self._save()

    def get_daily_levels(self, instrument):
        return self._data.get(instrument, {}).get("daily")

    def set_weekly_levels(self, instrument, high, low, week_key):
        self._data.setdefault(instrument, {})["weekly"] = {
            "high": high, "low": low, "week_key": week_key,
        }
        self._save()

    def get_weekly_levels(self, instrument):
        return self._data.get(instrument, {}).get("weekly")
