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
    if lower / rng > 0.6 and last["c"] > last["o"]:
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 20 + (lower / rng - 0.6) / 0.4 * 18))
        return {"pattern": "SD_REJECTION", "direction": "BUY",
                "sweep_price": float(last["l"]), "leg_extreme": float(last["l"]), "quality": quality}
    if upper / rng > 0.6 and last["c"] < last["o"]:
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 20 + (upper / rng - 0.6) / 0.4 * 18))
        return {"pattern": "SD_REJECTION", "direction": "SELL",
                "sweep_price": float(last["h"]), "leg_extreme": float(last["h"]), "quality": quality}
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
                        "sweep_price": float(head[1]), "leg_extreme": float(head[1]), "quality": quality}
    ls = _swings(window, "low")
    if len(ls) >= 3:
        l_sh, head, r_sh = ls[-3], ls[-2], ls[-1]
        if head[1] < l_sh[1] and head[1] < r_sh[1]:
            symmetry = 1 - abs(l_sh[1] - r_sh[1]) / max(head[1], 1e-9)
            if symmetry > (1 - tolerance):
                quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX, 25 + symmetry * 13))
                return {"pattern": "HEAD_SHOULDERS", "direction": "BUY",
                        "sweep_price": float(head[1]), "leg_extreme": float(head[1]), "quality": quality}
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
        return {"pattern": "FLAG", "direction": "BUY", "sweep_price": float(cons_high),
                "leg_extreme": float(cons_high), "quality": quality}
    if last["c"] < cons_low:
        return {"pattern": "FLAG", "direction": "SELL", "sweep_price": float(cons_low),
                "leg_extreme": float(cons_low), "quality": quality}
    return None


def detect_news_retest(df, lookback=15, spike_mult=cfg.NEWS_SPIKE_ATR_MULT):
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
    leg_extreme = float(spike["l"]) if direction == "BUY" else float(spike["h"])
    return {"pattern": "NEWS_RETEST", "direction": direction, "sweep_price": float(midpoint),
            "leg_extreme": leg_extreme, "quality": quality}


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
    return raw, "1.0R"


def compute_tp2(direction, entry, risk, levels):
    """§4 TP2 -- nearest pooled liquidity level beyond entry in the trade
    direction; falls back to entry + 1.8R if none exists."""
    raw = entry + cfg.TP2_R_MULT * risk if direction == "BUY" else entry - cfg.TP2_R_MULT * risk
    ahead = [lvl for lvl in levels if (lvl > entry if direction == "BUY" else lvl < entry)]
    if not ahead:
        return raw, False
    return (min(ahead), True) if direction == "BUY" else (max(ahead), True)


def compute_tp3(direction, entry, risk, tp2_price, levels):
    """§4 TP3 -- whichever is CLOSER to entry between the raw 2.8R target and
    the next external level beyond TP2 (prior-week H/L or nearest H4 swing);
    falls back to the raw target alone if no such external level exists."""
    raw = entry + cfg.TP3_R_MULT * risk if direction == "BUY" else entry - cfg.TP3_R_MULT * risk
    ahead = [lvl for lvl in levels if (lvl > tp2_price if direction == "BUY" else lvl < tp2_price)]
    if not ahead:
        return raw, False
    external = min(ahead) if direction == "BUY" else max(ahead)
    if abs(external - entry) < abs(raw - entry):
        return external, True
    return raw, False


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

    breakdown = {"pattern": candidate["pattern"], "pattern_quality": candidate["quality"]}
    total = candidate["quality"]

    total += technical_confirm_score(df_entry, direction)
    bias_pts, bias_tag = daily_bias_score(htf, direction)
    total += bias_pts
    breakdown["daily_bias"] = bias_tag
    total += vwap_filter_score(df_entry, direction, now_utc)

    kz_pts, kz_name = market_sessions.killzone_bonus(now_utc, instrument_class)
    total += kz_pts
    breakdown["killzone"] = kz_name

    total += ind.round_number_bonus(candidate["sweep_price"], instrument_class)
    poc, va_low, va_high = ind.volume_profile_zones(df_entry)
    vp_pts, vp_tag = ind.volume_profile_bonus(candidate["sweep_price"], poc, va_low, va_high)
    total += vp_pts
    breakdown["volume_profile"] = vp_tag
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

    # Capital.com's price objects can carry both bid and ask closes; use the
    # spread implied by the latest candle if present, else the SL buffer
    # relies solely on its ATR term (spread unavailable is treated as 0, not
    # an error -- this degrades gracefully rather than blocking the setup).
    spread = market["entry"][-1].get("spread") or 0.0
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

    if instrument == "BTCUSD":
        # No session structure for a 24/7 market -- PDH/PDL, Daily Open,
        # Weekly Open, prior-week H/L instead of Asian/London/NY ranges.
        d_open = market_sessions.daily_open(market["entry"], now_utc)
        w_open = market_sessions.weekly_open(market["entry"], now_utc)
        tp2_pool = [lvl for lvl in (pdh, pdl, d_open, w_open) if lvl is not None]
    else:
        asian_h, asian_l = market_sessions.session_range(market["entry"], now_utc, *market_sessions.ASIAN_SESSION)
        london_h, london_l = market_sessions.session_range(market["entry"], now_utc, *market_sessions.LONDON_SESSION)
        ny_h, ny_l = market_sessions.session_range(market["entry"], now_utc, *market_sessions.NY_SESSION)
        tp2_pool = [lvl for lvl in (pdh, pdl, asian_h, asian_l, london_h, london_l, ny_h, ny_l) if lvl is not None]
        tp2_pool += [z["price"] for z in eqh_eql_zones]
    tp2, tp2_from_level = compute_tp2(direction, entry, risk, tp2_pool)

    # TP3's "next external level" is universal (prior-week H/L + nearest H4
    # swing), regardless of instrument class.
    h4_swings = [p for _, p in _swings(_df(market["h4"]), "high" if direction == "BUY" else "low")]
    tp3_pool = [lvl for lvl in (pwh, pwl) if lvl is not None] + h4_swings
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
        "score": int(round(total)), "breakdown": breakdown, "htf_bias": htf, **exits,
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
