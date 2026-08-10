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

import pandas as pd

import market_sessions
import scoring_indicators as ind
import strategy_config as cfg
from strategy import modes
from strategy import whale_tracker
from strategy.smc_detector import find_smc_candidate

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


# ─────────────────────────────────────────────────────────────────────
# Multi-timeframe read-out (15m / 1h / 4h) — informational only. Attached
# to every qualifying alert so the reader can see how the shorter and
# higher timeframes line up with the trade direction. Does NOT gate or
# change when an alert fires.
# ─────────────────────────────────────────────────────────────────────
def _tf_trend(candles, flat_band_pct=0.0005):
    """Short trend read for one timeframe: EMA20 vs EMA50 on closes ->
    'up' / 'down' / 'flat'. Returns 'flat' when there isn't enough history."""
    df = _df(candles)
    if len(df) < 50:
        return "flat"
    e_fast = ind.ema(df["c"], 20).iloc[-1]
    e_slow = ind.ema(df["c"], 50).iloc[-1]
    if e_slow == 0:
        return "flat"
    diff = (e_fast - e_slow) / e_slow
    if abs(diff) < flat_band_pct:
        return "flat"
    return "up" if e_fast > e_slow else "down"


def multiframe_alignment(m15, h1, h4, direction):
    """{'15m'|'1h'|'4h': {'trend': up/down/flat, 'agree': aligned/against/flat}}
    for the three timeframes relative to the trade direction."""
    out = {}
    for label, candles in (("15m", m15), ("1h", h1), ("4h", h4)):
        trend = _tf_trend(candles)
        if trend == "flat":
            agree = "flat"
        else:
            with_dir = (trend == "up" and direction == "BUY") or (trend == "down" and direction == "SELL")
            agree = "aligned" if with_dir else "against"
        out[label] = {"trend": trend, "agree": agree}
    return out


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


def detect_liquidity_sweep_bos(df, lookback=cfg.LIQUIDITY_SWEEP_LOOKBACK):
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
                "sweep_price": float(swing_high), "leg_extreme": float(last["h"]), "quality": quality}
    if last["l"] < swing_low and last["c"] > swing_low:
        depth = (swing_low - last["l"]) / a
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 20 + min(18, depth * 20)))
        return {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                "sweep_price": float(swing_low), "leg_extreme": float(last["l"]), "quality": quality}
    return None


def detect_sd_rejection(df, lookback=20):
    if len(df) < lookback:
        return None
    last = df.iloc[-1]
    rng, upper, lower = _wick_stats(last)
    if rng <= 0:
        return None
    wick = cfg.SD_REJECTION_WICK_RATIO
    span = max(1.0 - wick, 1e-9)
    if lower / rng > wick and last["c"] > last["o"]:
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 20 + (lower / rng - wick) / span * 18))
        return {"pattern": "SD_REJECTION", "direction": "BUY",
                "sweep_price": float(last["l"]), "leg_extreme": float(last["l"]), "quality": quality,
                "quality_max": cfg.PATTERN_QUALITY_BASE_MAX}
    if upper / rng > wick and last["c"] < last["o"]:
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 20 + (upper / rng - wick) / span * 18))
        return {"pattern": "SD_REJECTION", "direction": "SELL",
                "sweep_price": float(last["h"]), "leg_extreme": float(last["h"]), "quality": quality,
                "quality_max": cfg.PATTERN_QUALITY_BASE_MAX}
    return None


def detect_head_shoulders(df, lookback=30, tolerance=cfg.HEAD_SHOULDERS_TOLERANCE):
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
                        "sweep_price": float(head[1]), "leg_extreme": float(head[1]), "quality": quality,
                        "quality_max": cfg.PATTERN_QUALITY_BASE_MAX}
    ls = _swings(window, "low")
    if len(ls) >= 3:
        l_sh, head, r_sh = ls[-3], ls[-2], ls[-1]
        if head[1] < l_sh[1] and head[1] < r_sh[1]:
            symmetry = 1 - abs(l_sh[1] - r_sh[1]) / max(head[1], 1e-9)
            if symmetry > (1 - tolerance):
                quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 25 + symmetry * 13))
                return {"pattern": "HEAD_SHOULDERS", "direction": "BUY",
                        "sweep_price": float(head[1]), "leg_extreme": float(head[1]), "quality": quality,
                        "quality_max": cfg.PATTERN_QUALITY_BASE_MAX}
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
    tight_max = cfg.FLAG_TIGHTNESS_MAX
    if tightness > tight_max:
        return None
    last = df.iloc[-1]
    cons_high, cons_low = consolidation.max() + consolidation.std(), consolidation.min() - consolidation.std()
    quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 22 + (tight_max - tightness) / tight_max * 16))
    if last["c"] > cons_high:
        return {"pattern": "FLAG", "direction": "BUY", "sweep_price": float(cons_high),
                "leg_extreme": float(cons_high), "quality": quality,
                "quality_max": cfg.PATTERN_QUALITY_BASE_MAX}
    if last["c"] < cons_low:
        return {"pattern": "FLAG", "direction": "SELL", "sweep_price": float(cons_low),
                "leg_extreme": float(cons_low), "quality": quality,
                "quality_max": cfg.PATTERN_QUALITY_BASE_MAX}
    return None


def detect_news_retest(df, lookback=15, spike_mult=cfg.NEWS_RETEST_SPIKE_MULT):
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
    prox_max = cfg.NEWS_RETEST_PROXIMITY
    if proximity > prox_max:
        return None
    quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 24 + (prox_max - proximity) / prox_max * 14))
    direction = "BUY" if spike["c"] > spike["o"] else "SELL"
    leg_extreme = float(spike["l"]) if direction == "BUY" else float(spike["h"])
    return {"pattern": "NEWS_RETEST", "direction": direction, "sweep_price": float(midpoint),
            "leg_extreme": leg_extreme, "quality": quality,
            "quality_max": cfg.PATTERN_QUALITY_BASE_MAX}


def _scalp_swings(df, kind, window=cfg.SCALP_SWING_LOOKBACK):
    """Micro-structure swing detection for the scalp detector (window=2)."""
    return _swings(df, kind, window=window)


def _find_liquidity_level(df, direction, atr_val, lookback=30):
    """G1 — find the nearest liquidity level (equal highs/lows, session
    extremes, or prior-day levels) in the direction of the expected sweep."""
    tol = cfg.SCALP_EQ_TOL_ATR * atr_val
    n = len(df)
    start = max(0, n - lookback)

    if direction == "BUY":
        swings = _scalp_swings(df.iloc[start:], "low")
        if len(swings) < 2:
            return None
        swing_prices = [p for _, p in swings]
        for i in range(len(swing_prices) - 1):
            for j in range(i + 1, len(swing_prices)):
                if abs(swing_prices[i] - swing_prices[j]) <= tol:
                    level = (swing_prices[i] + swing_prices[j]) / 2
                    if level < df["c"].iloc[-1]:
                        return level
        return min(swing_prices) if swing_prices else None
    else:
        swings = _scalp_swings(df.iloc[start:], "high")
        if len(swings) < 2:
            return None
        swing_prices = [p for _, p in swings]
        for i in range(len(swing_prices) - 1):
            for j in range(i + 1, len(swing_prices)):
                if abs(swing_prices[i] - swing_prices[j]) <= tol:
                    level = (swing_prices[i] + swing_prices[j]) / 2
                    if level > df["c"].iloc[-1]:
                        return level
        return max(swing_prices) if swing_prices else None


def detect_scalp_sweep_bos(df):
    """Strict 6-gate scalp detector: Sweep → Displacement → BOS → FVG/50% entry.

    All gates must pass in order. Returns a candidate with pre-computed
    exits (scalp_entry, scalp_stop, scalp_tp1, scalp_tp_final) that
    score_candidate uses instead of computing its own.

    Fixes BUG-1: requires an actual BOS (candle close beyond structure) —
    the old LIQUIDITY_SWEEP_BOS fired on sweep+rejection alone.
    """
    if len(df) < 30:
        return None
    atr_series = ind.atr(df)
    atr_val = atr_series.iloc[-1]
    if pd.isna(atr_val) or atr_val <= 0:
        return None

    for direction in ("BUY", "SELL"):
        result = _scalp_gate_sequence(df, direction, atr_val)
        if result is not None:
            return result
    return None


def _scalp_gate_sequence(df, direction, atr_val):
    """Run the 6 gates for one direction. Returns candidate or None."""
    is_buy = direction == "BUY"
    n = len(df)

    # G1 — Liquidity level
    liq_level = _find_liquidity_level(df, direction, atr_val)
    if liq_level is None:
        return None

    # Scan recent bars for the sweep + subsequent gates
    scan_start = max(0, n - 20)
    sweep_bar_idx = None
    sweep_extreme = None

    for idx in range(scan_start, n):
        bar = df.iloc[idx]
        # G2 — Sweep: penetrate beyond liquidity, reject back
        if is_buy:
            penetration = liq_level - bar["l"]
            if penetration < cfg.SCALP_SWEEP_MIN_PEN_ATR * atr_val:
                continue
            if bar["c"] <= liq_level:
                continue
            rng = bar["h"] - bar["l"]
            if rng <= 0:
                continue
            wick = min(bar["o"], bar["c"]) - bar["l"]
            if wick / rng < cfg.SCALP_SWEEP_REJECTION_RATIO:
                continue
            sweep_bar_idx = idx
            sweep_extreme = float(bar["l"])
        else:
            penetration = bar["h"] - liq_level
            if penetration < cfg.SCALP_SWEEP_MIN_PEN_ATR * atr_val:
                continue
            if bar["c"] >= liq_level:
                continue
            rng = bar["h"] - bar["l"]
            if rng <= 0:
                continue
            wick = bar["h"] - max(bar["o"], bar["c"])
            if wick / rng < cfg.SCALP_SWEEP_REJECTION_RATIO:
                continue
            sweep_bar_idx = idx
            sweep_extreme = float(bar["h"])

    if sweep_bar_idx is None:
        return None

    # G3 — Displacement: dominant candle in the trade direction within BOS_MAX_BARS
    bos_window_end = min(n, sweep_bar_idx + cfg.SCALP_BOS_MAX_BARS + 1)
    disp_found = False
    for idx in range(sweep_bar_idx, bos_window_end):
        bar = df.iloc[idx]
        body = abs(bar["c"] - bar["o"])
        rng = bar["h"] - bar["l"]
        if rng <= 0:
            continue
        correct_dir = (bar["c"] > bar["o"]) if is_buy else (bar["c"] < bar["o"])
        if not correct_dir:
            continue
        if body / rng >= cfg.SCALP_DISP_BODY_RATIO and body >= cfg.SCALP_DISP_MIN_ATR * atr_val:
            disp_found = True
            break
    if not disp_found:
        return None

    # G4 — BOS: candle CLOSE beyond the most recent structure swing
    structure_swings = _scalp_swings(df.iloc[:sweep_bar_idx], "high" if is_buy else "low")
    if not structure_swings:
        return None
    structure_level = structure_swings[-1][1]

    bos_close = None
    bos_idx = None
    for idx in range(sweep_bar_idx, bos_window_end):
        bar = df.iloc[idx]
        if is_buy and bar["c"] > structure_level:
            bos_close = float(bar["c"])
            bos_idx = idx
            break
        elif not is_buy and bar["c"] < structure_level:
            bos_close = float(bar["c"])
            bos_idx = idx
            break
    if bos_close is None:
        return None

    # Define the displacement leg
    if is_buy:
        leg_low = sweep_extreme
        leg_high = bos_close
    else:
        leg_high = sweep_extreme
        leg_low = bos_close
    leg_range = leg_high - leg_low
    if leg_range <= 0:
        return None

    # G5 — FVG within the displacement
    fvg_zone = None
    has_fvg = False
    fib_50 = leg_low + cfg.SCALP_ENTRY_FIB * leg_range if is_buy else leg_high - cfg.SCALP_ENTRY_FIB * leg_range

    for i in range(max(1, sweep_bar_idx), min(n - 1, bos_idx + 1)):
        prev_bar = df.iloc[i - 1]
        cur_bar = df.iloc[i]
        if i + 1 >= n:
            break
        next_bar = df.iloc[i + 1]
        if is_buy:
            if prev_bar["h"] < next_bar["l"]:
                gap_lo, gap_hi = float(prev_bar["h"]), float(next_bar["l"])
                gap_size = gap_hi - gap_lo
                if gap_size >= cfg.SCALP_FVG_MIN_ATR * atr_val:
                    if gap_lo >= sweep_extreme and gap_hi <= leg_high:
                        if fvg_zone is None or abs((gap_lo + gap_hi) / 2 - fib_50) < abs((fvg_zone[0] + fvg_zone[1]) / 2 - fib_50):
                            fvg_zone = (gap_lo, gap_hi)
                            has_fvg = True
        else:
            if prev_bar["l"] > next_bar["h"]:
                gap_hi, gap_lo = float(prev_bar["l"]), float(next_bar["h"])
                gap_size = gap_hi - gap_lo
                if gap_size >= cfg.SCALP_FVG_MIN_ATR * atr_val:
                    if gap_hi <= sweep_extreme and gap_lo >= leg_low:
                        if fvg_zone is None or abs((gap_lo + gap_hi) / 2 - fib_50) < abs((fvg_zone[0] + fvg_zone[1]) / 2 - fib_50):
                            fvg_zone = (gap_lo, gap_hi)
                            has_fvg = True

    # Entry zone: FVG if available, else fib_50 band
    if has_fvg:
        entry_lo, entry_hi = fvg_zone
    else:
        band = cfg.SCALP_FVG_MIN_ATR * atr_val
        entry_lo, entry_hi = fib_50 - band, fib_50 + band

    # G6 — Entry trigger: price retraces into entry zone with confirmation close
    entry_window_end = min(n, bos_idx + cfg.SCALP_ENTRY_MAX_BARS + 1)
    entry_price = None
    for idx in range(bos_idx + 1, entry_window_end):
        bar = df.iloc[idx]
        in_zone = (bar["l"] <= entry_hi) if is_buy else (bar["h"] >= entry_lo)
        if not in_zone:
            continue
        confirmed = (bar["c"] > bar["o"]) if is_buy else (bar["c"] < bar["o"])
        if confirmed and entry_lo <= bar["c"] <= entry_hi + leg_range * 0.1:
            entry_price = float(bar["c"])
            break
        elif confirmed:
            entry_price = fib_50
            break

    if entry_price is None:
        # If we're still within the entry window (not expired), use fib_50
        bars_since_bos = n - 1 - bos_idx
        if bars_since_bos <= cfg.SCALP_ENTRY_MAX_BARS:
            entry_price = fib_50
        else:
            return None

    # Compute exits per spec
    stop = sweep_extreme - cfg.SCALP_STOP_ATR_BUFFER * atr_val if is_buy else sweep_extreme + cfg.SCALP_STOP_ATR_BUFFER * atr_val
    risk = abs(entry_price - stop)
    if risk <= 0:
        return None

    tp1 = entry_price + cfg.SCALP_TP1_R_MULT * risk if is_buy else entry_price - cfg.SCALP_TP1_R_MULT * risk
    tp2 = entry_price + cfg.SCALP_TP2_R_MULT * risk if is_buy else entry_price - cfg.SCALP_TP2_R_MULT * risk
    tp_final = entry_price + cfg.SCALP_TP_FINAL_R_MULT * risk if is_buy else entry_price - cfg.SCALP_TP_FINAL_R_MULT * risk

    # Quality based on how many confirmations we got
    scalp_quality_max = 35
    quality = 28
    if has_fvg:
        quality += 4
    if disp_found:
        quality += 3
    quality = min(scalp_quality_max, quality)

    return {
        "pattern": "SCALP_SWEEP_BOS",
        "direction": direction,
        "sweep_price": float(liq_level),
        "leg_extreme": sweep_extreme,
        "quality": quality,
        "quality_max": scalp_quality_max,
        "has_fvg": has_fvg,
        "scalp_entry": round(entry_price, 5),
        "scalp_stop": round(stop, 5),
        "scalp_tp1": round(tp1, 5),
        "scalp_tp2": round(tp2, 5),
        "scalp_tp_final": round(tp_final, 5),
        "scalp_leg_origin": sweep_extreme,
        "scalp_leg_end": bos_close,
        "scalp_structure_level": float(structure_level),
        "scalp_bos_close": bos_close,
    }


PATTERN_DETECTORS = [
    detect_sd_rejection,
    detect_head_shoulders,
    detect_flag,
    detect_news_retest,
    detect_scalp_sweep_bos,
]


def _normalized_quality(candidate):
    """Quality as a fraction of the detector's ceiling, for fair cross-detector
    comparison (a scalp at 35/35 should beat an SD_REJECTION at 35/38)."""
    qmax = candidate.get("quality_max", cfg.PATTERN_QUALITY_BASE_MAX)
    return candidate["quality"] / qmax if qmax > 0 else 0


def find_candidate(entry_candles):
    """Run all pattern detectors — the scalp detector (strict 6-gate),
    3 SMC-library detectors, plus the original 4 — and return the
    highest-quality match (normalized across detector ceilings)."""
    df = _df(entry_candles)
    best = None
    best_norm = -1
    for detector in PATTERN_DETECTORS:
        result = detector(df)
        if result:
            norm = _normalized_quality(result)
            if norm > best_norm:
                best = result
                best_norm = norm
    smc_result = find_smc_candidate(entry_candles)
    if smc_result:
        norm = _normalized_quality(smc_result)
        if norm > best_norm:
            best = smc_result
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


def vwap_filter_score(df, direction, now_utc=None):
    """Is current price on the correct side of the volume-weighted (not just
    time-weighted) session reference line -- replaces the old EMA20 filter
    with a reference that reflects where real transacted volume sits, not
    just a smoothed close-price average."""
    vwap = ind.anchored_vwap(df, now_utc)
    if vwap is None:
        return cfg.VWAP_FILTER_NEUTRAL
    price = df["c"].iloc[-1]
    if direction == "BUY":
        if price > vwap:
            return cfg.VWAP_FILTER_MATCH
        if price < vwap:
            return cfg.VWAP_FILTER_AGAINST
    else:
        if price < vwap:
            return cfg.VWAP_FILTER_MATCH
        if price > vwap:
            return cfg.VWAP_FILTER_AGAINST
    return cfg.VWAP_FILTER_NEUTRAL


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


def recent_spike_penalty(df, atr_value, candidate_pattern,
                          lookback=cfg.RECENT_SPIKE_LOOKBACK, mult=cfg.NEWS_SPIKE_ATR_MULT):
    """A big recent range spike (news-like) makes the other 4 detectors — which
    assume orderly price action — more likely to be false post-news chop.
    NEWS_RETEST already explicitly trades that exact spike, so it's exempt."""
    if candidate_pattern == "NEWS_RETEST" or pd.isna(atr_value) or atr_value <= 0:
        return 0
    if len(df) < lookback + 1:
        return 0
    recent = df.iloc[-(lookback + 1):-1]
    ranges = recent["h"] - recent["l"]
    if (ranges >= mult * atr_value).any():
        return cfg.RECENT_SPIKE_PENALTY
    return 0


# ─────────────────────────────────────────────────────────────────────
# Entry / SL / TP construction (Entry/SL/TP Selection Rules v1.3)
#
# Pure point-selection over an already-qualifying candidate -- pattern
# detection/scoring above this point is untouched. Everything below derives
# entry/SL/TP purely from the entry-timeframe candles + direction, via its
# own BOS/fractal discovery, independent of which of the 5 detectors fired.
# ─────────────────────────────────────────────────────────────────────
def find_leg(candles, direction, max_lookback=cfg.BOS_SEARCH_LOOKBACK_BARS):
    """Locate the most recent confirmed BOS (Break of Structure) in
    `direction`: a candle CLOSE beyond the nearest prior minor-swing fractal
    (2-2 window) of the opposite kind. Scans backward from the newest candle
    so the result is always the freshest qualifying leg.

    leg_origin = extreme of the sweep wick (the low that got swept, for a
    long) and leg_end = extreme of the move as of the BOS candle's close --
    frozen there even if price has moved further since; recomputing on a
    later candle would make entry/SL/TP non-reproducible.

    Returns {"leg_origin", "leg_end", "bos_index"} or None if no BOS is
    confirmed anywhere in the searchable window.
    """
    df = _df(candles)
    n = len(df)
    if n < 6:
        return None
    fractal_highs = _swings(df, "high")
    fractal_lows = _swings(df, "low")
    floor_idx = max(0, n - max_lookback)

    for bos_idx in range(n - 1, floor_idx - 1, -1):
        close = df["c"].iloc[bos_idx]
        if direction == "BUY":
            prior_highs = [p for i, p in fractal_highs if i < bos_idx]
            if not prior_highs or close <= prior_highs[-1]:
                continue
            prior_lows = [i for i, p in fractal_lows if i < bos_idx]
            if not prior_lows:
                continue
            origin_idx = prior_lows[-1]
            leg_origin = float(df["l"].iloc[origin_idx: bos_idx + 1].min())
            leg_end = float(df["h"].iloc[origin_idx: bos_idx + 1].max())
        else:
            prior_lows = [p for i, p in fractal_lows if i < bos_idx]
            if not prior_lows or close >= prior_lows[-1]:
                continue
            prior_highs = [i for i, p in fractal_highs if i < bos_idx]
            if not prior_highs:
                continue
            origin_idx = prior_highs[-1]
            leg_origin = float(df["h"].iloc[origin_idx: bos_idx + 1].max())
            leg_end = float(df["l"].iloc[origin_idx: bos_idx + 1].min())

        if leg_end == leg_origin:
            continue
        return {"leg_origin": leg_origin, "leg_end": leg_end, "bos_index": bos_idx}
    return None


def _retrace_price(leg_origin, leg_end, direction, pct):
    """Price at `pct` retracement back from leg_end toward leg_origin."""
    if direction == "BUY":
        return leg_end - pct * (leg_end - leg_origin)
    return leg_end + pct * (leg_origin - leg_end)


def compute_entry(leg_origin, leg_end, direction, fvg_zones=None):
    """§1 -- 50% retrace entry, overridden by an FVG's midpoint if a
    same-direction FVG sits fully inside the leg with its midpoint landing
    in the 40-62% retrace zone. Among qualifying FVGs, the one whose
    midpoint is nearest the raw 50% level wins (deterministic tie-break)."""
    entry = _retrace_price(leg_origin, leg_end, direction, cfg.ENTRY_RETRACE_PCT)
    entry_basis = "50% leg retrace"

    zone_a = _retrace_price(leg_origin, leg_end, direction, cfg.ENTRY_FVG_ZONE_MIN_PCT)
    zone_b = _retrace_price(leg_origin, leg_end, direction, cfg.ENTRY_FVG_ZONE_MAX_PCT)
    zone_lo, zone_hi = min(zone_a, zone_b), max(zone_a, zone_b)
    leg_lo, leg_hi = min(leg_origin, leg_end), max(leg_origin, leg_end)

    if fvg_zones:
        qualifying = []
        for z in fvg_zones:
            if z["bottom"] < leg_lo or z["top"] > leg_hi:
                continue  # not fully inside the leg
            mid = (z["top"] + z["bottom"]) / 2
            if zone_lo <= mid <= zone_hi:
                qualifying.append(mid)
        if qualifying:
            entry = min(qualifying, key=lambda mid: abs(mid - entry))
            entry_basis = "FVG midpoint"

    return entry, entry_basis


def compute_stop(leg_origin, direction, atr_value, spread, instrument):
    """§3 -- buffer = max(0.5xATR, 2xspread) behind leg_origin, then an
    anti-stop-hunt push of an extra 0.15xATR if that lands within the
    instrument's round-number proximity threshold."""
    buffer = max(cfg.SL_BUFFER_ATR_MULT * atr_value, cfg.SL_BUFFER_SPREAD_MULT * spread)
    stop = leg_origin - buffer if direction == "BUY" else leg_origin + buffer

    round_mult, proximity = cfg.ROUND_NUMBER_OFFSET_TABLE.get(instrument, (None, None))
    if round_mult:
        nearest = round(stop / round_mult) * round_mult
        if abs(stop - nearest) <= proximity:
            extra = cfg.ROUND_NUMBER_OFFSET_ATR_MULT * atr_value
            stop = stop - extra if direction == "BUY" else stop + extra
    return stop


def _tp1_exception_level(direction, entry, risk, fvg_zones, swing_prices):
    """§4 TP1 exception -- an unfilled FVG (near edge) or a minor swing price
    sitting in [entry + 0.8R, entry + 1.0R) (mirrored for shorts) overrides
    the raw 1.0R target. Nearest-to-entry wins among qualifying candidates
    (the most conservative partial-profit level)."""
    if direction == "BUY":
        lo, hi = entry + cfg.TP1_EXCEPTION_MIN_R * risk, entry + cfg.TP1_EXCEPTION_MAX_R * risk
    else:
        lo, hi = entry - cfg.TP1_EXCEPTION_MAX_R * risk, entry - cfg.TP1_EXCEPTION_MIN_R * risk

    candidates = []
    for z in (fvg_zones or []):
        near_edge = z["bottom"] if direction == "BUY" else z["top"]
        if lo <= near_edge < hi:
            candidates.append(near_edge)
    for price in swing_prices:
        if lo <= price < hi:
            candidates.append(price)
    if not candidates:
        return None
    return min(candidates, key=lambda p: abs(p - entry))


def compute_tp1(direction, entry, risk, fvg_zones, swing_prices):
    raw = entry + cfg.TP1_R_MULT * risk if direction == "BUY" else entry - cfg.TP1_R_MULT * risk
    level = _tp1_exception_level(direction, entry, risk, fvg_zones, swing_prices)
    if level is not None:
        return level, "FVG/swing exception"
    return raw, f"{cfg.TP1_R_MULT:.1f}R"


def compute_tp2(direction, entry, risk, levels, tp1_price=None):
    """§4 TP2 -- nearest pooled liquidity level beyond TP1 (not just beyond
    entry) in the trade direction; falls back to entry + 1.8R if none
    exists. tp1_price defaults to entry for callers that don't have it, but
    every real caller must pass the actual computed TP1 -- pooled liquidity
    levels are independent of the ATR-based R-multiples, so a level that is
    merely "ahead of entry" can easily land BETWEEN entry and TP1, which
    would make TP2 trigger before TP1 in real price action (a genuine
    production bug, confirmed against live alerts where TP1 ended up
    farther from entry than both TP2 and TP3)."""
    raw = entry + cfg.TP2_R_MULT * risk if direction == "BUY" else entry - cfg.TP2_R_MULT * risk
    floor = entry if tp1_price is None else tp1_price
    ahead = [lvl for lvl in levels if (lvl > floor if direction == "BUY" else lvl < floor)]
    if not ahead:
        return raw, False
    return (min(ahead), True) if direction == "BUY" else (max(ahead), True)


def compute_tp3(direction, entry, risk, tp2_price, levels):
    """§4 TP3 -- the closer-to-entry (conservative) of the raw TP3_R_MULT
    target and the next external level beyond TP2 (prior-week H/L or nearest
    H4 swing) -- but ONLY among candidates that actually sit beyond TP2, so
    the TP1<TP2<TP3 ordering can never invert. When TP2 is a pooled liquidity
    level farther out than the raw R-target and no external level clears it,
    TP3 is placed a fixed half-R beyond TP2 rather than collapsing back to a
    raw target that would sit behind TP2."""
    raw = entry + cfg.TP3_R_MULT * risk if direction == "BUY" else entry - cfg.TP3_R_MULT * risk
    is_buy = direction == "BUY"
    beyond_tp2 = (lambda p: p > tp2_price) if is_buy else (lambda p: p < tp2_price)

    ahead = [lvl for lvl in levels if beyond_tp2(lvl)]
    external = (min(ahead) if is_buy else max(ahead)) if ahead else None

    candidates = []
    if beyond_tp2(raw):
        candidates.append((raw, False))
    if external is not None:
        candidates.append((external, True))
    if not candidates:
        # Neither the raw R-target nor any external level clears TP2: park TP3
        # a fixed half-R past TP2 so the runner target is always the farthest.
        tp3 = tp2_price + 0.5 * risk if is_buy else tp2_price - 0.5 * risk
        return tp3, False
    return min(candidates, key=lambda pair: abs(pair[0] - entry))


# ─────────────────────────────────────────────────────────────────────
# score_candidate() — the full pipeline
# ─────────────────────────────────────────────────────────────────────
def score_candidate(instrument, instrument_class, candidate, market, now_utc, level_store,
                     confirmation_bonus=0, diagnostic=False, mode=None, whale_transactions=None):
    """
    market: {'entry': [...15m candles], 'h1': [...], 'h4': [...], 'daily': [...]}
    Returns a dict with the full score breakdown + entry/stop/targets, or None if
    the setup is hard-blocked or scores below the no-alert floor.

    diagnostic=True never returns None: every hard-block or below-threshold
    case instead returns a dict with a "blocked" reason and "score" (None if
    the block happened before a score could be computed at all). Used to show
    near-miss scores on /scan without changing normal alerting behavior.

    mode: an optional strategy.modes.ModeConfig; defaults to modes.STANDARD
    (today's behavior) when omitted.
    """
    m = mode or modes.STANDARD
    direction = candidate["direction"]

    # Section 2 — PRIORITY FIX: counter-trend hard block
    htf = htf_bias(market["h4"])
    if htf == "TRENDING_UP" and direction == "SELL":
        if diagnostic:
            return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                     "score": None, "htf_bias": htf, "blocked": "counter-trend (H4 uptrend blocks SELL)"}
        return None
    if htf == "TRENDING_DOWN" and direction == "BUY":
        if diagnostic:
            return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                     "score": None, "htf_bias": htf, "blocked": "counter-trend (H4 downtrend blocks BUY)"}
        return None

    df_entry = _df(market["entry"])
    a = ind.atr(df_entry).iloc[-1]
    if pd.isna(a) or a <= 0:
        if diagnostic:
            return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                     "score": None, "htf_bias": htf, "blocked": "invalid ATR"}
        return None

    quality_max = candidate.get("quality_max", cfg.PATTERN_QUALITY_BASE_MAX)
    normalized_quality = round(candidate["quality"] / quality_max * cfg.PATTERN_QUALITY_NORMALIZED_MAX) if quality_max > 0 else 0
    breakdown = {"pattern": candidate["pattern"], "pattern_quality": candidate["quality"],
                 "pattern_quality_normalized": normalized_quality}
    total = normalized_quality

    total += technical_confirm_score(df_entry, direction)
    bias_pts, bias_tag = daily_bias_score(htf, direction)
    total += bias_pts
    breakdown["daily_bias"] = bias_tag

    # Volume-derived factors (anchored VWAP filter + volume-profile bonus) are
    # only meaningful where the volume feed is meaningful. On CFD index
    # instruments the "volume" is broker tick-count on a synthetic price, so
    # these factors are noise there and must never influence the decision --
    # they are applied to CRYPTO (BTCUSD) only, and even there only as small
    # confirmations, never a primary driver.
    is_crypto = instrument_class == "CRYPTO"
    if is_crypto:
        total += vwap_filter_score(df_entry, direction, now_utc)

    kz_pts, kz_name = market_sessions.killzone_bonus(now_utc, instrument_class)
    total += kz_pts
    breakdown["killzone"] = kz_name

    total += ind.round_number_bonus(candidate["sweep_price"], instrument_class)
    if is_crypto:
        poc, va_low, va_high = ind.volume_profile_zones(df_entry)
        vp_pts, vp_tag = ind.volume_profile_bonus(candidate["sweep_price"], poc, va_low, va_high)
        total += vp_pts
        breakdown["volume_profile"] = vp_tag
    else:
        breakdown["volume_profile"] = None
    total += choppy_market_penalty(df_entry)

    spike_penalty = recent_spike_penalty(df_entry, a, candidate["pattern"])
    total += spike_penalty
    breakdown["recent_spike"] = spike_penalty != 0

    atr_penalty, atr_state = ind.atr_sweet_spot_penalty(df_entry, mode=m)
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

    ifvg_pts, ifvg_zone = ind.ifvg_bonus(candidate["sweep_price"], direction, market["h1"])
    total += ifvg_pts
    breakdown["ifvg"] = ifvg_zone is not None

    eqh_eql_zones = ind.detect_eqh_eql_zones(market["h1"])
    eq_pts, eq_zone = ind.eqh_eql_bonus(candidate["sweep_price"], eqh_eql_zones)
    total += eq_pts
    breakdown["eqh_eql"] = eq_zone is not None

    if instrument == "BTCUSD":
        netflow_usd, _ = whale_tracker.compute_exchange_netflow(whale_transactions)
        whale_pts, whale_tag = whale_tracker.whale_flow_bonus(direction, netflow_usd)
        total += whale_pts
        breakdown["whale_flow"] = whale_tag

    total += confirmation_bonus
    breakdown["confirmation_bonus"] = confirmation_bonus

    if total < m.watch_min_score:
        if diagnostic:
            return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                     "score": int(round(total)), "htf_bias": htf,
                     "blocked": f"below WATCH threshold ({m.watch_min_score})"}
        return None

    pdh = daily.get("high") if daily else None
    pdl = daily.get("low") if daily else None
    pwh = weekly.get("high") if weekly else None
    pwl = weekly.get("low") if weekly else None

    spread = market["entry"][-1].get("spread") or 0.0

    # TP2/TP3 liquidity pools (shared by SMC and generic exit paths)
    if instrument == "BTCUSD":
        d_open = market_sessions.daily_open(market["entry"], now_utc)
        w_open = market_sessions.weekly_open(market["entry"], now_utc)
        tp2_pool = [lvl for lvl in (pdh, pdl, d_open, w_open) if lvl is not None]
    else:
        asian_h, asian_l = market_sessions.session_range(market["entry"], now_utc, *market_sessions.ASIAN_SESSION)
        london_h, london_l = market_sessions.session_range(market["entry"], now_utc, *market_sessions.LONDON_SESSION)
        ny_h, ny_l = market_sessions.session_range(market["entry"], now_utc, *market_sessions.NY_SESSION)
        tp2_pool = [lvl for lvl in (pdh, pdl, asian_h, asian_l, london_h, london_l, ny_h, ny_l) if lvl is not None]
        tp2_pool += [z["price"] for z in eqh_eql_zones]
    h4_swings = [p for _, p in _swings(_df(market["h4"]), "high" if direction == "BUY" else "low")]
    tp3_pool = [lvl for lvl in (pwh, pwl) if lvl is not None] + h4_swings

    is_scalp = candidate["pattern"] == "SCALP_SWEEP_BOS"
    _SMC_PATTERNS = {"ORDER_BLOCK", "CHOCH_REVERSAL", "SMC_LIQUIDITY_SWEEP"}
    is_smc = candidate["pattern"] in _SMC_PATTERNS and candidate.get("smc_entry") is not None

    if is_scalp:
        entry = candidate["scalp_entry"]
        stop = candidate["scalp_stop"]
        risk = abs(entry - stop)
        if risk <= 0:
            if diagnostic:
                return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                         "score": int(round(total)), "htf_bias": htf,
                         "blocked": "non-positive risk (scalp entry/stop construction failed)"}
            return None
        if spread > cfg.SCALP_MAX_SPREAD_R_FRAC * risk:
            if diagnostic:
                return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                         "score": int(round(total)), "htf_bias": htf,
                         "blocked": f"spread {spread:.2f} exceeds {cfg.SCALP_MAX_SPREAD_R_FRAC*100:.0f}% of R ({risk:.2f})"}
            return None
        tp1 = candidate["scalp_tp1"]
        tp2 = candidate["scalp_tp2"]
        tp3 = candidate["scalp_tp_final"]
        leg_origin = candidate["scalp_leg_origin"]
        leg_end = candidate["scalp_leg_end"]
        exits = {
            "entry_price": round(entry, 5), "stop_loss": round(stop, 5),
            "tp1": round(tp1, 5), "tp2": round(tp2, 5), "tp3": round(tp3, 5),
            "entry_basis": "scalp 50% FVG retrace", "tp1_basis": f"{cfg.SCALP_TP1_R_MULT:.1f}R (scalp)",
            "tp2_capped": False, "tp3_capped": False,
            "leg_origin": round(leg_origin, 5), "leg_end": round(leg_end, 5),
        }
    elif is_smc:
        entry = candidate["smc_entry"]
        stop = candidate["smc_stop"]
        risk = abs(entry - stop)
        if risk <= 0:
            if diagnostic:
                return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                         "score": int(round(total)), "htf_bias": htf,
                         "blocked": "non-positive risk (SMC entry/stop construction failed)"}
            return None
        tp1 = candidate["smc_tp1"]
        tp1_basis = f"{cfg.TP1_R_MULT:.1f}R (SMC)"
        entry_basis = candidate.get("smc_entry_basis", "SMC level")
        tp2, tp2_from_level = compute_tp2(direction, entry, risk, tp2_pool, tp1_price=tp1)
        tp3, tp3_from_level = compute_tp3(direction, entry, risk, tp2, tp3_pool)
        leg_origin = candidate.get("leg_extreme", entry)
        leg_end = tp1
        exits = {
            "entry_price": round(entry, 5), "stop_loss": round(stop, 5),
            "tp1": round(tp1, 5), "tp2": round(tp2, 5), "tp3": round(tp3, 5),
            "entry_basis": entry_basis, "tp1_basis": tp1_basis,
            "tp2_capped": tp2_from_level, "tp3_capped": tp3_from_level,
            "leg_origin": round(float(leg_origin), 5), "leg_end": round(float(leg_end), 5),
        }
    else:
        leg = find_leg(market["entry"], direction)
        if leg is None:
            if diagnostic:
                return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                         "score": int(round(total)), "htf_bias": htf,
                         "blocked": "no confirmed BOS in recent history"}
            return None
        leg_origin, leg_end = leg["leg_origin"], leg["leg_end"]

        wanted_fvg_dir = "BULLISH" if direction == "BUY" else "BEARISH"
        m15_fvg_zones = [z for z in ind.detect_fvg_zones(market["entry"]) if z["direction"] == wanted_fvg_dir]
        entry, entry_basis = compute_entry(leg_origin, leg_end, direction, fvg_zones=m15_fvg_zones)

        stop = compute_stop(leg_origin, direction, a, spread, instrument)
        risk = abs(entry - stop)
        if risk <= 0:
            if diagnostic:
                return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                         "score": int(round(total)), "htf_bias": htf,
                         "blocked": "non-positive risk (entry/stop construction failed)"}
            return None

        m15_swing_prices = [p for _, p in _swings(df_entry, "high" if direction == "BUY" else "low")]
        tp1, tp1_basis = compute_tp1(direction, entry, risk, m15_fvg_zones, m15_swing_prices)
        tp2, tp2_from_level = compute_tp2(direction, entry, risk, tp2_pool, tp1_price=tp1)
        tp3, tp3_from_level = compute_tp3(direction, entry, risk, tp2, tp3_pool)

        exits = {
            "entry_price": round(entry, 5), "stop_loss": round(stop, 5),
            "tp1": round(tp1, 5), "tp2": round(tp2, 5), "tp3": round(tp3, 5),
            "entry_basis": entry_basis, "tp1_basis": tp1_basis,
            "tp2_capped": tp2_from_level, "tp3_capped": tp3_from_level,
            "leg_origin": round(leg_origin, 5), "leg_end": round(leg_end, 5),
        }

    result = {
        "instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
        "score": int(round(total)), "breakdown": breakdown, "htf_bias": htf,
        "timeframes": multiframe_alignment(market.get("m15", []), market["h1"], market["h4"], direction),
        **exits,
    }
    if diagnostic:
        result["blocked"] = None
    return result


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
