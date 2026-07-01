"""
Trading Alert Bot — scoring engine.

Section 2  — counter-trend hard-block fix (implemented first, see PRIORITY FIX).
Section 5  — 7 upgrade modules folded into score_candidate().
Section 1.3 — base pattern-quality / technical-confirm / MA20 / choppiness factors.

score_candidate() is the single entry point used by main_alerts.py for both the
WATCH path (fast, no confirmation wait) and the A+ path (3-candle confirmation,
Section 5.6, handled by the Pending-A+ store below).
"""
import json
import os
import random

import pandas as pd

import market_sessions
import scoring_indicators as ind
import strategy_config as cfg

STATE_DIR = "state"
PENDING_APLUS_PATH = os.path.join(STATE_DIR, "pending_aplus.json")


def _df(candles):
    return pd.DataFrame(candles)


# ─────────────────────────────────────────────────────────────────────
# Section 2 — H4 regime / higher-timeframe bias
# ─────────────────────────────────────────────────────────────────────
def htf_bias(candles_h4, flat_band_pct=0.001):
    """TRENDING_UP / TRENDING_DOWN / RANGING via H4 EMA 50/200."""
    df = _df(candles_h4)
    if len(df) < 200:
        return "RANGING"
    e50 = ind.ema(df["c"], 50).iloc[-1]
    e200 = ind.ema(df["c"], 200).iloc[-1]
    if e200 == 0:
        return "RANGING"
    diff_pct = (e50 - e200) / e200
    if abs(diff_pct) < flat_band_pct:
        return "RANGING"
    return "TRENDING_UP" if e50 > e200 else "TRENDING_DOWN"


def daily_bias_score(htf, direction):
    """Section 1.3 — +15 with-trend / +5 neutral / -8 counter-trend.
    Note: counter-trend combinations against a TRENDING regime never reach this
    function — they are hard-blocked in score_candidate() per Section 2."""
    if htf == "RANGING":
        return cfg.DAILY_BIAS_NEUTRAL, "neutral"
    with_trend = (htf == "TRENDING_UP" and direction == "BUY") or (
        htf == "TRENDING_DOWN" and direction == "SELL"
    )
    if with_trend:
        return cfg.DAILY_BIAS_WITH_TREND, "with_trend"
    return cfg.DAILY_BIAS_COUNTER_TREND, "counter_trend"


# ─────────────────────────────────────────────────────────────────────
# Pattern-quality detectors (Section 1.3 — 5 patterns, kept as-is baseline)
# ─────────────────────────────────────────────────────────────────────
def _wick_stats(candle):
    rng = candle["h"] - candle["l"]
    upper = candle["h"] - max(candle["o"], candle["c"])
    lower = min(candle["o"], candle["c"]) - candle["l"]
    return rng, upper, lower


def detect_liquidity_sweep_bos(df, lookback=20):
    if len(df) < lookback + 3:
        return None
    a = ind.atr(df).iloc[-1]
    if pd.isna(a) or a <= 0:
        return None
    window = df.iloc[-(lookback + 2):-2]
    swing_high, swing_low = window["h"].max(), window["l"].min()
    last = df.iloc[-1]
    if last["h"] > swing_high and last["c"] < swing_high:
        depth = (last["h"] - swing_high) / a
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 20 + min(18, depth * 20)))
        return {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "SELL",
                "sweep_price": float(swing_high), "quality": quality}
    if last["l"] < swing_low and last["c"] > swing_low:
        depth = (swing_low - last["l"]) / a
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 20 + min(18, depth * 20)))
        return {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                "sweep_price": float(swing_low), "quality": quality}
    return None


def detect_sd_rejection(df, lookback=20):
    if len(df) < lookback:
        return None
    last = df.iloc[-1]
    rng, upper, lower = _wick_stats(last)
    if rng <= 0:
        return None
    if lower / rng > 0.6 and last["c"] > last["o"]:
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 20 + (lower / rng - 0.6) / 0.4 * 18))
        return {"pattern": "SD_REJECTION", "direction": "BUY",
                "sweep_price": float(last["l"]), "quality": quality}
    if upper / rng > 0.6 and last["c"] < last["o"]:
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 20 + (upper / rng - 0.6) / 0.4 * 18))
        return {"pattern": "SD_REJECTION", "direction": "SELL",
                "sweep_price": float(last["h"]), "quality": quality}
    return None


def detect_head_shoulders(df, lookback=30, tolerance=0.2):
    if len(df) < lookback:
        return None
    window = df.tail(lookback).reset_index(drop=True)
    # top (bearish) H&S — three swing highs, middle highest
    hs = _swings(window, "high")
    if len(hs) >= 3:
        l_sh, head, r_sh = hs[-3], hs[-2], hs[-1]
        if head[1] > l_sh[1] and head[1] > r_sh[1]:
            symmetry = 1 - abs(l_sh[1] - r_sh[1]) / head[1]
            if symmetry > (1 - tolerance):
                quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 25 + symmetry * 13))
                return {"pattern": "HEAD_SHOULDERS", "direction": "SELL",
                        "sweep_price": float(head[1]), "quality": quality}
    ls = _swings(window, "low")
    if len(ls) >= 3:
        l_sh, head, r_sh = ls[-3], ls[-2], ls[-1]
        if head[1] < l_sh[1] and head[1] < r_sh[1]:
            symmetry = 1 - abs(l_sh[1] - r_sh[1]) / max(head[1], 1e-9)
            if symmetry > (1 - tolerance):
                quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 25 + symmetry * 13))
                return {"pattern": "HEAD_SHOULDERS", "direction": "BUY",
                        "sweep_price": float(head[1]), "quality": quality}
    return None


def _swings(df, kind, window=2):
    col = "h" if kind == "high" else "l"
    out = []
    for i in range(window, len(df) - window):
        seg = df[col].iloc[i - window: i + window + 1]
        val = df[col].iloc[i]
        if kind == "high" and val == seg.max():
            out.append((i, float(val)))
        elif kind == "low" and val == seg.min():
            out.append((i, float(val)))
    return out


def detect_flag(df, lookback=15):
    if len(df) < lookback + 1:
        return None
    a = ind.atr(df).iloc[-1]
    if pd.isna(a) or a <= 0:
        return None
    consolidation = df["c"].iloc[-(lookback + 1):-1]
    tightness = consolidation.std() / a
    if tightness > 0.5:
        return None
    last = df.iloc[-1]
    cons_high, cons_low = consolidation.max() + consolidation.std(), consolidation.min() - consolidation.std()
    quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 22 + (0.5 - tightness) / 0.5 * 16))
    if last["c"] > cons_high:
        return {"pattern": "FLAG", "direction": "BUY", "sweep_price": float(cons_high), "quality": quality}
    if last["c"] < cons_low:
        return {"pattern": "FLAG", "direction": "SELL", "sweep_price": float(cons_low), "quality": quality}
    return None


def detect_news_retest(df, lookback=15, spike_mult=2.5):
    if len(df) < lookback + 2:
        return None
    a = ind.atr(df).iloc[-1]
    if pd.isna(a) or a <= 0:
        return None
    segment = df.iloc[-(lookback + 2):-2]
    ranges = segment["h"] - segment["l"]
    if ranges.empty:
        return None
    spike_idx = ranges.idxmax()
    spike = df.loc[spike_idx]
    if (spike["h"] - spike["l"]) < spike_mult * a:
        return None
    midpoint = (spike["h"] + spike["l"]) / 2
    last = df.iloc[-1]
    proximity = abs(last["c"] - midpoint) / a
    if proximity > 0.5:
        return None
    quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 24 + (0.5 - proximity) / 0.5 * 14))
    direction = "BUY" if spike["c"] > spike["o"] else "SELL"
    return {"pattern": "NEWS_RETEST", "direction": direction, "sweep_price": float(midpoint), "quality": quality}


PATTERN_DETECTORS = [
    detect_liquidity_sweep_bos,
    detect_sd_rejection,
    detect_head_shoulders,
    detect_flag,
    detect_news_retest,
]


def find_candidate(entry_candles):
    """Run all 5 pattern detectors on the entry-timeframe candles and return the
    highest-quality match, or None."""
    df = _df(entry_candles)
    best = None
    for detector in PATTERN_DETECTORS:
        result = detector(df)
        if result and (best is None or result["quality"] > best["quality"]):
            best = result
    return best


# ─────────────────────────────────────────────────────────────────────
# Technical confirm / MA20 filter / choppiness / volume (Section 1.3)
# ─────────────────────────────────────────────────────────────────────
def technical_confirm_score(df, direction):
    close = df["c"]
    r = ind.rsi(close).iloc[-1]
    macd_line, signal_line, _ = ind.macd(close)
    ema20 = ind.ema(close, 20).iloc[-1]
    aligned = 0
    if direction == "BUY":
        aligned += r > 50
        aligned += macd_line.iloc[-1] > signal_line.iloc[-1]
        aligned += close.iloc[-1] > ema20
    else:
        aligned += r < 50
        aligned += macd_line.iloc[-1] < signal_line.iloc[-1]
        aligned += close.iloc[-1] < ema20
    if aligned >= 2:
        return cfg.TECHNICAL_CONFIRM_ALL_ALIGNED
    if aligned == 1:
        return cfg.TECHNICAL_CONFIRM_ONE_ALIGNED
    return cfg.TECHNICAL_CONFIRM_NONE_ALIGNED


def ma20_filter_score(df, direction):
    ema20 = ind.ema(df["c"], 20).iloc[-1]
    price = df["c"].iloc[-1]
    if direction == "BUY":
        if price > ema20:
            return cfg.MA20_FILTER_MATCH
        if price < ema20:
            return cfg.MA20_FILTER_AGAINST
    else:
        if price < ema20:
            return cfg.MA20_FILTER_MATCH
        if price > ema20:
            return cfg.MA20_FILTER_AGAINST
    return cfg.MA20_FILTER_NEUTRAL


def choppiness_index(df, period=14):
    tr = pd.concat(
        [df["h"] - df["l"], (df["h"] - df["c"].shift(1)).abs(), (df["l"] - df["c"].shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr_sum = tr.tail(period).sum()
    hh = df["h"].tail(period).max()
    ll = df["l"].tail(period).min()
    if hh == ll or atr_sum <= 0:
        return 0.0
    import math
    return 100 * math.log10(atr_sum / (hh - ll)) / math.log10(period)


def choppy_market_penalty(df, period=14, threshold=61.8):
    chop = choppiness_index(df, period)
    return cfg.CHOPPY_MARKET_PENALTY if chop > threshold else 0


def volume_confirmation_bonus(df, direction, lookback=20):
    if "v" not in df.columns or df["v"].isna().all():
        return 0
    avg_vol = df["v"].tail(lookback).mean()
    last_vol = df["v"].iloc[-1]
    return cfg.VOLUME_CONFIRM_BONUS if last_vol > avg_vol else 0


# ─────────────────────────────────────────────────────────────────────
# Entry / stop / target calculation (Section 1.5)
# ─────────────────────────────────────────────────────────────────────
def compute_entry_exit(candidate, breakout_candle, atr_value, rng=random):
    direction = candidate["direction"]
    sweep_price = candidate["sweep_price"]
    o, c = breakout_candle["o"], breakout_candle["c"]
    entry = o + (c - o) * cfg.RETRACE_FRACTION
    if atr_value > 0 and abs(entry - sweep_price) / atr_value > cfg.RETRACE_DEEPEN_ATR_MULT:
        entry = sweep_price  # deepen to S/R level retest

    jitter = rng.uniform(-0.05, 0.05) * atr_value
    if direction == "BUY":
        stop = entry - atr_value + jitter
        risk = entry - stop
        tp1 = entry + risk * cfg.MIN_RR_RATIO
        tp2 = entry + risk * (cfg.MIN_RR_RATIO + 1)
    else:
        stop = entry + atr_value + jitter
        risk = stop - entry
        tp1 = entry - risk * cfg.MIN_RR_RATIO
        tp2 = entry - risk * (cfg.MIN_RR_RATIO + 1)
    return {
        "entry_price": round(entry, 5), "stop_loss": round(stop, 5),
        "tp1": round(tp1, 5), "tp2": round(tp2, 5), "rr_ratio": cfg.MIN_RR_RATIO,
    }


# ─────────────────────────────────────────────────────────────────────
# score_candidate() — the full pipeline
# ─────────────────────────────────────────────────────────────────────
def score_candidate(instrument, instrument_class, candidate, market, now_utc, level_store,
                     confirmation_bonus=0):
    """
    market: {'entry': [...15m candles], 'h1': [...], 'h4': [...], 'daily': [...]}
    Returns a dict with the full score breakdown + entry/stop/targets, or None if
    the setup is hard-blocked or scores below the no-alert floor.
    """
    direction = candidate["direction"]

    # Section 2 — PRIORITY FIX: counter-trend hard block
    htf = htf_bias(market["h4"])
    if htf == "TRENDING_UP" and direction == "SELL":
        return None
    if htf == "TRENDING_DOWN" and direction == "BUY":
        return None

    df_entry = _df(market["entry"])
    a = ind.atr(df_entry).iloc[-1]
    if pd.isna(a) or a <= 0:
        return None

    breakdown = {"pattern": candidate["pattern"], "pattern_quality": candidate["quality"]}
    total = candidate["quality"]

    total += technical_confirm_score(df_entry, direction)
    bias_pts, bias_tag = daily_bias_score(htf, direction)
    total += bias_pts
    breakdown["daily_bias"] = bias_tag
    total += ma20_filter_score(df_entry, direction)

    kz_pts, kz_name = market_sessions.killzone_bonus(now_utc, instrument_class)
    total += kz_pts
    breakdown["killzone"] = kz_name

    total += ind.round_number_bonus(candidate["sweep_price"], instrument_class)
    total += volume_confirmation_bonus(df_entry, direction)
    total += choppy_market_penalty(df_entry)

    atr_penalty, atr_state = ind.atr_sweet_spot_penalty(df_entry)
    total += atr_penalty
    breakdown["atr_state"] = atr_state

    daily = level_store.get_daily_levels(instrument)
    if daily:
        pdh_pts, pdh_tag = ind.pdh_pdl_bonus(candidate["sweep_price"], daily.get("high"), daily.get("low"))
        total += pdh_pts
        breakdown["pdh_pdl"] = pdh_tag

    weekly = level_store.get_weekly_levels(instrument)
    if weekly:
        wk_pts, wk_tag = ind.monday_weekly_sweep_bonus(
            candidate["sweep_price"], weekly.get("high"), weekly.get("low"), now_utc)
        total += wk_pts
        breakdown["weekly_sweep"] = wk_tag

    fvg_pts, fvg_zone = ind.fvg_bonus(candidate["sweep_price"], direction, market["h1"])
    total += fvg_pts
    breakdown["fvg"] = fvg_zone is not None

    eqh_eql_zones = ind.detect_eqh_eql_zones(market["h1"])
    eq_pts, eq_zone = ind.eqh_eql_bonus(candidate["sweep_price"], eqh_eql_zones)
    total += eq_pts
    breakdown["eqh_eql"] = eq_zone is not None

    total += confirmation_bonus
    breakdown["confirmation_bonus"] = confirmation_bonus

    if total < cfg.WATCH_MIN_SCORE:
        return None

    exits = compute_entry_exit(candidate, df_entry.iloc[-1], a)
    return {
        "instrument": instrument, "direction": direction, "score": int(round(total)),
        "breakdown": breakdown, "htf_bias": htf, **exits,
    }


# ─────────────────────────────────────────────────────────────────────
# Section 5.6 — 3-candle confirmation filter for A+ signals
# ─────────────────────────────────────────────────────────────────────
class PendingAPlusStore:
    """WATCH alerts fire instantly. A+ signals wait one candle close for
    confirmation before being sent — this store tracks setups in that wait state."""

    def __init__(self, path=PENDING_APLUS_PATH):
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

    def add(self, instrument, scored):
        self._data[instrument] = scored
        self._save()

    def get(self, instrument):
        return self._data.get(instrument)

    def remove(self, instrument):
        self._data.pop(instrument, None)
        self._save()

    def all(self):
        return dict(self._data)


def confirmation_closed_in_direction(last_closed_candle, direction):
    if direction == "BUY":
        return last_closed_candle["c"] > last_closed_candle["o"]
    return last_closed_candle["c"] < last_closed_candle["o"]
