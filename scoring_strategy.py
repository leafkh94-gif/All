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
from strategy import modes

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
# Entry / stop / target calculation (Bot Spec V4 Sections 1-3)
# ─────────────────────────────────────────────────────────────────────
def compute_entry_exit(candidate, breakout_candle, atr_value, retrace_pct, fvg_zones=None, rng=random):
    """Leg-based entry: retrace_pct of the real (wick-inclusive) impulse leg,
    anchored at candidate['leg_extreme'] through the breakout candle's close.
    A same-direction FVG fully inside the leg overrides the retrace level
    entirely (enter at its near edge -- FVGs fill faster and more reliably
    than an arbitrary retrace percentage). Stop is structural, anchored just
    behind the leg extreme rather than an arbitrary ATR distance from entry.

    Returns None (caller must skip the setup, not degrade it) if the leg is
    degenerate, the computed entry doesn't land strictly inside the leg, or
    the resulting risk is non-positive.
    """
    direction = candidate["direction"]
    leg_extreme = candidate["leg_extreme"]
    close = breakout_candle["c"]

    if direction == "BUY":
        leg_low, leg_high = leg_extreme, close
    else:
        leg_low, leg_high = close, leg_extreme
    if leg_high <= leg_low:
        return None  # degenerate leg -- breakout close never cleared the extreme

    raw_leg_size = leg_high - leg_low
    # Some patterns' leg_extreme sits on the same candle as the breakout
    # close (e.g. a sweep-and-reject candle), so the raw leg is really just
    # that one candle's own range -- far smaller than the actual move behind
    # the setup. Floor the size used to SCALE the retrace/stop math (not the
    # sanity-clamp bounds below, which stay tied to the real close/extreme)
    # so a same-candle leg still produces a realistic pullback distance and
    # stop buffer instead of an entry that barely differs from market price.
    leg_size = max(raw_leg_size, cfg.MIN_LEG_ATR_MULT * atr_value) if atr_value > 0 else raw_leg_size

    entry = close - leg_size * retrace_pct if direction == "BUY" else close + leg_size * retrace_pct
    entry_basis = f"{retrace_pct:.0%} leg retrace"

    if fvg_zones:
        contained = [
            z for z in fvg_zones
            if z["bottom"] >= leg_low and z["top"] <= leg_high
            and (z["top"] - z["bottom"]) >= cfg.MIN_FVG_SIZE_ATR_MULT * atr_value
        ]
        if contained:
            near_edge = (lambda z: z["top"]) if direction == "BUY" else (lambda z: z["bottom"])
            nearest = min(contained, key=lambda z: abs(close - near_edge(z)))
            entry = near_edge(nearest)
            entry_basis = "FVG edge"

    if not (leg_low < entry < leg_high):
        return None  # sanity clamp -- entry must lie strictly inside the REAL leg

    jitter = rng.uniform(-0.05, 0.05) * atr_value
    # Stop buffer: floored at a flat ATR fraction (raised from the old flat
    # 0.25x) AND scaled to a fraction of the (floored) leg size, so bigger
    # moves get proportionally more room the way an experienced trader would
    # size a stop, rather than every setup getting the same thin pad.
    stop_buffer = max(cfg.STOP_BUFFER_MIN_ATR_MULT * atr_value, cfg.STOP_BUFFER_LEG_FRACTION * leg_size)
    if direction == "BUY":
        stop = leg_extreme - stop_buffer + jitter
        risk = entry - stop
    else:
        stop = leg_extreme + stop_buffer + jitter
        risk = stop - entry
    if risk <= 0:
        return None

    if direction == "BUY":
        tp1 = entry + risk * cfg.MIN_RR_RATIO
        tp2 = entry + risk * (cfg.MIN_RR_RATIO + 1)
    else:
        tp1 = entry - risk * cfg.MIN_RR_RATIO
        tp2 = entry - risk * (cfg.MIN_RR_RATIO + 1)

    return {
        "entry_price": round(entry, 5), "stop_loss": round(stop, 5),
        "tp1": round(tp1, 5), "tp2": round(tp2, 5), "rr_ratio": cfg.MIN_RR_RATIO,
        "entry_basis": entry_basis, "stop_basis": "structural (behind leg extreme)",
    }


# ─────────────────────────────────────────────────────────────────────
# score_candidate() — the full pipeline
# ─────────────────────────────────────────────────────────────────────
def score_candidate(instrument, instrument_class, candidate, market, now_utc, level_store,
                     confirmation_bonus=0, diagnostic=False, mode=None):
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

    total += confirmation_bonus
    breakdown["confirmation_bonus"] = confirmation_bonus

    if total < m.watch_min_score:
        if diagnostic:
            return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                     "score": int(round(total)), "htf_bias": htf,
                     "blocked": f"below WATCH threshold ({m.watch_min_score})"}
        return None

    retrace_pct = cfg.INSTRUMENT_PROFILES.get(instrument, {}).get("retrace_pct", 0.5)
    wanted_fvg_dir = "BULLISH" if direction == "BUY" else "BEARISH"
    entry_fvg_zones = [z for z in ind.detect_fvg_zones(market["entry"]) if z["direction"] == wanted_fvg_dir]
    exits = compute_entry_exit(candidate, df_entry.iloc[-1], a, retrace_pct, fvg_zones=entry_fvg_zones)
    if exits is None:
        if diagnostic:
            return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                     "score": int(round(total)), "htf_bias": htf,
                     "blocked": "degenerate leg (entry construction failed)"}
        return None

    pdh = daily.get("high") if daily else None
    pdl = daily.get("low") if daily else None
    pwh = weekly.get("high") if weekly else None
    pwl = weekly.get("low") if weekly else None
    liquidity_levels = ind.collect_liquidity_levels(
        direction, exits["entry_price"], pdh, pdl, pwh, pwl, eqh_eql_zones, poc, va_low, va_high)
    risk = abs(exits["entry_price"] - exits["stop_loss"])

    # TP1 also gets liquidity awareness now, not just TP2 -- but only cap it
    # if the capped level still clears a sane partial-profit floor. No real
    # nearby structure justifies exiting earlier than that just because it
    # happens to be the first thing in the pooled level list.
    capped_tp1, tp1_capped = ind.cap_target_at_liquidity(direction, exits["entry_price"], exits["tp1"], liquidity_levels)
    exits["tp1_capped"] = False
    if tp1_capped:
        tp1_rr = abs(capped_tp1 - exits["entry_price"]) / risk if risk else 0
        if tp1_rr >= cfg.MIN_RR_TP1_AFTER_CAP:
            exits["tp1"] = round(capped_tp1, 5)
            exits["tp1_capped"] = True

    # TP2 targets the next liquidity level beyond wherever TP1 actually
    # landed, not the same pool TP1 already used -- otherwise a single
    # nearby level would cap both to the identical price, a confusing and
    # redundant partial-close instruction. If TP1 wasn't capped (no level
    # cleared its own floor), measure "beyond" from entry instead, same as
    # if TP1 had never been touched.
    tp2_boundary = exits["tp1"] if exits["tp1_capped"] else exits["entry_price"]
    tp2_levels = [lvl for lvl in liquidity_levels
                  if (lvl > tp2_boundary if direction == "BUY" else lvl < tp2_boundary)]
    capped_tp2, tp2_capped = ind.cap_target_at_liquidity(direction, exits["entry_price"], exits["tp2"], tp2_levels)
    exits["tp2_capped"] = tp2_capped
    if tp2_capped:
        exits["tp2"] = round(capped_tp2, 5)
        capped_rr = abs(exits["tp2"] - exits["entry_price"]) / risk if risk else 0
        if capped_rr < cfg.MIN_RR_AFTER_CAP:
            if diagnostic:
                return {"instrument": instrument, "direction": direction, "pattern": candidate["pattern"],
                         "score": int(round(total)), "htf_bias": htf,
                         "blocked": "RR_BELOW_MIN_AFTER_LIQUIDITY_CAP"}
            return None

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
