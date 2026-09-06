"""
Shared stop-loss / take-profit construction.

Both detectors used to build their own ladder inline, which meant a
change to the risk structure had to be made twice and the two could
silently drift. They now both call build_targets().

Two modes, selectable by cfg.TARGET_MODE:

  FIXED  — the original fixed dollar ladder: SL $25, TP1 $25 (1R),
           TP2 $50 (2R), TP3 $100 (4R). Simple, but the same distance in
           a dead Asian range and an FOMC hour.

  ATR    — the same 1R/2R/4R ladder measured in ATR. The stop is
           ATR_SL_MULT * ATR(M15), clamped to [ATR_SL_MIN_POINTS,
           ATR_SL_MAX_POINTS] so a spike in ATR can't produce a silly
           distance and a dead market can't produce one inside the
           spread.

Neither mode is asserted to be better here. They exist as a matched pair
so `backtest.py --target-mode {FIXED,ATR}` can decide the question on
data instead of on assertion.
"""
import pandas as pd

import scoring_indicators as ind
import strategy_config as cfg


def stop_distance(atr_value=None, mode=None):
    """Risk distance in price for the configured target mode."""
    m = mode or cfg.TARGET_MODE
    if m == "ATR":
        if not atr_value or atr_value <= 0:
            # No ATR available -- fall back to the fixed distance rather
            # than inventing one. Callers that care can check target_mode
            # in the returned dict.
            return cfg.FIXED_SL_POINTS * cfg.POINT_VALUE
        raw = cfg.ATR_SL_MULT * float(atr_value)
        lo = cfg.ATR_SL_MIN_POINTS * cfg.POINT_VALUE
        hi = cfg.ATR_SL_MAX_POINTS * cfg.POINT_VALUE
        return max(lo, min(hi, raw))
    return cfg.FIXED_SL_POINTS * cfg.POINT_VALUE


def build_targets(entry, direction, atr_value=None, mode=None):
    """Return {stop_loss, tp1, tp2, tp3, risk, target_mode}.

    `mode` overrides cfg.TARGET_MODE (used by the backtester's
    --target-mode flag so a single process can compare both).
    """
    m = mode or cfg.TARGET_MODE
    entry = float(entry)
    risk = stop_distance(atr_value, mode=m)
    if risk <= 0:
        return None

    if m == "ATR":
        r1, r2, r3 = cfg.ATR_TP1_R, cfg.ATR_TP2_R, cfg.ATR_TP3_R
        d1, d2, d3 = risk * r1, risk * r2, risk * r3
    else:
        pt = cfg.POINT_VALUE
        d1 = cfg.FIXED_TP1_POINTS * pt
        d2 = cfg.FIXED_TP2_POINTS * pt
        d3 = cfg.FIXED_TP3_POINTS * pt

    sign = 1.0 if direction == "BUY" else -1.0
    return {
        "stop_loss": entry - sign * risk,
        "tp1": entry + sign * d1,
        "tp2": entry + sign * d2,
        "tp3": entry + sign * d3,
        "risk": risk,
        "target_mode": m,
    }


def atr_from_candles(candles, period=None):
    """Latest ATR from a candle list / DataFrame, or None if unavailable."""
    if candles is None:
        return None
    df = candles if isinstance(candles, pd.DataFrame) else pd.DataFrame(candles)
    if len(df) < (period or cfg.ATR_TARGET_PERIOD) + 1:
        return None
    a = ind.atr(df, period or cfg.ATR_TARGET_PERIOD)
    v = a.iloc[-1]
    if pd.isna(v) or v <= 0:
        return None
    return float(v)
