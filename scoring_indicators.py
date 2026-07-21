"""
Trading Alert Bot — indicators and pattern/level detectors.
RSI/MACD/EMA/ATR, PDH/PDL, weekly levels, FVG, EQH/EQL, round-number proximity.
See Bot_Spec_V3 Sections 1.3, 5.1, 5.3, 5.4, 5.5, 5.7.
"""
import json
import os
from datetime import datetime, timezone

import pandas as pd

import strategy_config as cfg
from strategy import modes

STATE_DIR = "state"
LEVELS_PATH = os.path.join(STATE_DIR, "levels.json")

# FOREX_JPY gets its own step since JPY pairs quote at ~2 decimals (pip=0.01)
# rather than EURUSD-style ~4-5 decimals (pip=0.0001) -- 0.50 here is the same
# "50 pips" round-level concept as FOREX's 0.0050, just rescaled to JPY pips.
# ASIA_INDEX (JP225/HK50/A50) spans a wide range of absolute price levels
# (roughly 12,000-45,000) just like US_INDEX already does across US500/
# US100/US30 -- reusing the same 500-point step is consistent with that
# existing precedent, not a new approximation.
ROUND_NUMBER_STEP = {"US_INDEX": 500, "CRYPTO": 5000, "FOREX": 0.0050, "FOREX_JPY": 0.50, "ASIA_INDEX": 500}
ROUND_NUMBER_PROXIMITY_PCT = 0.001  # within 0.1% of the nearest round level


def candles_to_df(candles):
    return pd.DataFrame(candles)


# ─────────────────────────────────────────────────────────────────────
# Core indicators
# ─────────────────────────────────────────────────────────────────────
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))


def macd(series, fast=12, slow=26, signal=9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


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
    """Section 5.7 — penalize dead (< 10th pct) and chaotic (> 80th pct) ATR regimes."""
    m = mode or modes.STANDARD
    pct = atr_percentile(df, lookback, period)
    if pct < m.atr_low_percentile:
        return cfg.ATR_DEAD_MARKET_PENALTY, "dead_market"
    if pct > m.atr_high_percentile:
        return cfg.ATR_TOO_VOLATILE_PENALTY, "too_volatile"
    return 0, "normal"


# ─────────────────────────────────────────────────────────────────────
# Anchored (session) VWAP and a simplified volume profile.
# Only OHLCV candles are available (no tick/order-flow data), so "typical
# price" (h+l+c)/3 stands in for trade price -- the standard approximation
# when true VWAP inputs aren't available.
# ─────────────────────────────────────────────────────────────────────
def anchored_vwap(df, now_utc=None):
    """Volume-weighted average price anchored to the start of the current UTC
    day (a session VWAP). Returns None if there's no usable volume data or no
    candles from today yet -- callers must treat that as "can't judge", not
    as a directional signal."""
    if df.empty or "v" not in df.columns or "t" not in df.columns:
        return None
    now_utc = now_utc or datetime.now(timezone.utc)
    anchor = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    cum_pv, cum_v = 0.0, 0.0
    for _, row in df.iterrows():
        v = row.get("v")
        if not v:
            continue
        try:
            t = datetime.fromisoformat(row["t"])
        except (TypeError, ValueError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t < anchor:
            continue
        typical = (row["h"] + row["l"] + row["c"]) / 3
        cum_pv += typical * v
        cum_v += v
    if cum_v <= 0:
        return None
    return cum_pv / cum_v


def volume_profile_zones(df, num_bins=20, value_area_pct=0.70):
    """Simplified volume profile: bucket each candle's (l, h) range into
    `num_bins` price bins spanning the df's full range, splitting the
    candle's volume evenly across the bins its range touches. Returns
    (poc_price, value_area_low, value_area_high) -- the point of control
    (highest-volume bin) and the price band expanded outward from it until
    `value_area_pct` of total volume is captured. Returns (None, None, None)
    if there's no usable volume data."""
    if df.empty or "v" not in df.columns or df["v"].isna().all():
        return None, None, None
    lo, hi = float(df["l"].min()), float(df["h"].max())
    if hi <= lo:
        return None, None, None
    bin_width = (hi - lo) / num_bins
    bins = [0.0] * num_bins
    for _, row in df.iterrows():
        v = row.get("v")
        if not v:
            continue
        start_bin = max(0, min(num_bins - 1, int((row["l"] - lo) / bin_width)))
        end_bin = max(0, min(num_bins - 1, int((row["h"] - lo) / bin_width)))
        span = end_bin - start_bin + 1
        for b in range(start_bin, end_bin + 1):
            bins[b] += v / span

    total_v = sum(bins)
    if total_v <= 0:
        return None, None, None
    poc_bin = max(range(num_bins), key=lambda b: bins[b])
    poc_price = lo + (poc_bin + 0.5) * bin_width

    target = total_v * value_area_pct
    lo_b = hi_b = poc_bin
    acc = bins[poc_bin]
    while acc < target and (lo_b > 0 or hi_b < num_bins - 1):
        left_v = bins[lo_b - 1] if lo_b > 0 else -1
        right_v = bins[hi_b + 1] if hi_b < num_bins - 1 else -1
        if right_v >= left_v:
            hi_b += 1
            acc += bins[hi_b]
        else:
            lo_b -= 1
            acc += bins[lo_b]
    return poc_price, lo + lo_b * bin_width, lo + (hi_b + 1) * bin_width


def volume_profile_bonus(price, poc, va_low, va_high):
    """+3 (VOLUME_CONFIRM_BONUS) if the sweep level sits inside the value
    area -- real transacted volume agrees this is a meaningful level, versus
    the old crude "last bar's volume above its 20-bar average" check."""
    if poc is None:
        return 0, None
    if va_low <= price <= va_high:
        return cfg.VOLUME_CONFIRM_BONUS, "in_value_area"
    return 0, None


# ─────────────────────────────────────────────────────────────────────
# Round-number proximity (Section 1.3)
# ─────────────────────────────────────────────────────────────────────
def round_number_bonus(price, instrument_class):
    step = ROUND_NUMBER_STEP.get(instrument_class)
    if not step or price <= 0:
        return 0
    nearest = round(price / step) * step
    if nearest == 0:
        return 0
    distance_pct = abs(price - nearest) / price
    return cfg.ROUND_NUMBER_BONUS if distance_pct <= ROUND_NUMBER_PROXIMITY_PCT else 0


# ─────────────────────────────────────────────────────────────────────
# Level persistence — PDH/PDL and weekly high/low (Sections 5.1, 5.5)
# ─────────────────────────────────────────────────────────────────────
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


def pdh_pdl_bonus(price, pdh, pdl):
    """Section 5.1 — +10 if sweep level is within 0.1% of previous day high/low."""
    if pdh and abs(price - pdh) / pdh <= cfg.PDH_PDL_PROXIMITY_PCT:
        return cfg.PDH_PDL_BONUS, "PDH"
    if pdl and abs(price - pdl) / pdl <= cfg.PDH_PDL_PROXIMITY_PCT:
        return cfg.PDH_PDL_BONUS, "PDL"
    return 0, None


def monday_weekly_sweep_bonus(price, week_high, week_low, now_utc):
    """Section 5.5 — Monday 00:00-18:00 UTC sweep at previous week high/low -> +12."""
    if now_utc.weekday() != 0 or now_utc.hour >= cfg.MONDAY_SWEEP_WINDOW_END_UTC_HOUR:
        return 0, None
    if week_high and abs(price - week_high) / week_high <= cfg.PDH_PDL_PROXIMITY_PCT:
        return cfg.MONDAY_SWEEP_BONUS, "WEEK_HIGH"
    if week_low and abs(price - week_low) / week_low <= cfg.PDH_PDL_PROXIMITY_PCT:
        return cfg.MONDAY_SWEEP_BONUS, "WEEK_LOW"
    return 0, None


# ─────────────────────────────────────────────────────────────────────
# Fair Value Gap detector (Section 5.3) — H1 3-candle imbalance
# ─────────────────────────────────────────────────────────────────────
def detect_fvg_zones(candles_h1, sweep_index=None, max_lookback=cfg.FVG_LOOKBACK_CANDLES):
    """Return FVG zones as {'direction', 'top', 'bottom', 'index'} found by scanning
    candle triples i, i+1, i+2. `sweep_index` (if given) limits the scan to the
    `max_lookback` candles preceding the sweep candle."""
    df = candles_to_df(candles_h1)
    end = sweep_index if sweep_index is not None else len(df) - 2
    start = max(0, end - max_lookback)
    zones = []
    for i in range(start, max(start, end - 1)):
        if i + 2 >= len(df):
            break
        c0, c2 = df.iloc[i], df.iloc[i + 2]
        if c2["l"] > c0["h"]:
            zones.append({"direction": "BULLISH", "bottom": float(c0["h"]), "top": float(c2["l"]), "index": i})
        elif c2["h"] < c0["l"]:
            zones.append({"direction": "BEARISH", "bottom": float(c2["h"]), "top": float(c0["l"]), "index": i})
    return zones


def fvg_bonus(entry_price, direction, candles_h1, sweep_index=None):
    """Section 5.3 — +8 if the 50% retrace entry price sits inside the nearest FVG."""
    zones = detect_fvg_zones(candles_h1, sweep_index)
    wanted = "BULLISH" if direction == "BUY" else "BEARISH"
    for z in zones:
        if z["direction"] == wanted and z["bottom"] <= entry_price <= z["top"]:
            return cfg.FVG_BONUS, z
    return 0, None


def detect_ifvg_zones(candles_h1, sweep_index=None, max_lookback=cfg.FVG_LOOKBACK_CANDLES):
    """Inverse FVG: an FVG that price has since closed fully through in the
    direction that invalidates it (a full candle close beyond its far side),
    at which point the same zone can act as support/resistance in the
    OPPOSITE direction from the original gap. Returns zones in the same
    {'direction', 'top', 'bottom', 'index'} shape as detect_fvg_zones, with
    `direction` already flipped to the new (inverse) polarity."""
    df = candles_to_df(candles_h1)
    end = sweep_index if sweep_index is not None else len(df) - 2
    zones = detect_fvg_zones(candles_h1, sweep_index, max_lookback)
    ifvg_zones = []
    for z in zones:
        for i in range(z["index"] + 2, min(end + 1, len(df))):
            close = df.iloc[i]["c"]
            if z["direction"] == "BULLISH" and close < z["bottom"]:
                ifvg_zones.append({"direction": "BEARISH", "bottom": z["bottom"], "top": z["top"], "index": z["index"]})
                break
            if z["direction"] == "BEARISH" and close > z["top"]:
                ifvg_zones.append({"direction": "BULLISH", "bottom": z["bottom"], "top": z["top"], "index": z["index"]})
                break
    return ifvg_zones


def ifvg_bonus(entry_price, direction, candles_h1, sweep_index=None):
    """+8 (IFVG_BONUS) if the 50% retrace entry price sits inside a flipped
    (inverse) FVG zone matching the trade direction."""
    zones = detect_ifvg_zones(candles_h1, sweep_index)
    wanted = "BULLISH" if direction == "BUY" else "BEARISH"
    for z in zones:
        if z["direction"] == wanted and z["bottom"] <= entry_price <= z["top"]:
            return cfg.IFVG_BONUS, z
    return 0, None


# ─────────────────────────────────────────────────────────────────────
# Equal Highs / Equal Lows detector (Section 5.4)
# ─────────────────────────────────────────────────────────────────────
def _swing_points(df, kind, window=2):
    """Simple fractal swing high/low detector using a +/-window bar comparison."""
    points = []
    col = "h" if kind == "high" else "l"
    for i in range(window, len(df) - window):
        seg = df[col].iloc[i - window: i + window + 1]
        val = df[col].iloc[i]
        if kind == "high" and val == seg.max():
            points.append((i, float(val)))
        elif kind == "low" and val == seg.min():
            points.append((i, float(val)))
    return points


def detect_eqh_eql_zones(candles_h1, lookback=cfg.EQH_EQL_LOOKBACK_CANDLES,
                          tolerance_pct=cfg.EQH_EQL_TOLERANCE_PCT):
    """Return EQH/EQL zones: two or more swing highs/lows within `tolerance_pct` of
    each other over the last `lookback` H1 candles."""
    df = candles_to_df(candles_h1).tail(lookback).reset_index(drop=True)
    zones = []
    for kind, tag in (("high", "EQH"), ("low", "EQL")):
        points = _swing_points(df, kind)
        used = [False] * len(points)
        for i in range(len(points)):
            if used[i]:
                continue
            cluster = [points[i][1]]
            for j in range(i + 1, len(points)):
                if used[j]:
                    continue
                if abs(points[j][1] - points[i][1]) / points[i][1] <= tolerance_pct:
                    cluster.append(points[j][1])
                    used[j] = True
            if len(cluster) >= 2:
                zones.append({"type": tag, "price": sum(cluster) / len(cluster), "touches": len(cluster)})
    return zones


def eqh_eql_bonus(price, zones, tolerance_pct=cfg.EQH_EQL_TOLERANCE_PCT):
    """Section 5.4 — +10 if sweep fires near a pre-identified EQH/EQL zone."""
    for z in zones:
        if abs(price - z["price"]) / z["price"] <= tolerance_pct:
            return cfg.EQH_EQL_BONUS, z
    return 0, None


