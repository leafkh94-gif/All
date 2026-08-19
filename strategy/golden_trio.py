"""Golden Trio strategy — sequenced RSI reversal at a Turtle band, ZLSMA
direction filter, chop-market rejection. The candidate this returns is
scored downstream in scoring_strategy.score_candidate.

Hard gates (return None if any fails):
  1. Not chop (recent range >= GT_CHOP_MIN_RANGE_ATR * ATR)
  2. Sequenced RSI reversal:
     BUY -> RSI dipped <= GT_RSI_DIP_LEVEL in last GT_RSI_DIP_LOOKBACK bars,
     then rose for GT_RSI_RISE_BARS consecutive bars, trigger bar closes
     above GT_RSI_CONFIRM_LEVEL from below, trigger body is bullish.
     SELL mirrors on the opposite side.
  3. Turtle band tested: the trigger bar's extreme (low for BUY, high for
     SELL) sits within GT_PROXIMITY_ATR_MULT * ATR of the band.
  4. ZLSMA slope is not against the direction. Slope classifies as:
       aligned / flat / against
     - against -> None (never fires)
     - flat -> allowed but scored as flat downstream (blocks A+)
     - aligned -> full points downstream

Component quality scores handed to the scorer:
  - rsi_confirm_quality: 0..SCORE_RSI_CONFIRM_MAX based on how deep the dip
    was and how clean the rise sequence was
  - turtle_quality: 0..SCORE_TURTLE_MAX based on how tightly the trigger
    bar's extreme hugged the band
  - zlsma_status: "aligned" | "flat"

Fixed target mode (cfg.TARGET_MODE == "FIXED") produces stop/TP1/TP2/TP3
at fixed point offsets; structural mode uses Turtle-band-derived targets.
"""
import pandas as pd

import scoring_indicators as ind
import strategy_config as cfg

PATTERN_NAME = "GOLDEN_TRIO"


# ─────────────────────────────────────────────────────────────────────
# Sequenced RSI reversal gate
# ─────────────────────────────────────────────────────────────────────
def _rsi_reversal_sequence(rsi_series, side):
    """Return (fires: bool, quality: 0..1, extreme_value: float) describing
    the reversal quality on the trigger bar.

    BUY sequence:
      1. RSI dipped <= GT_RSI_DIP_LEVEL somewhere in the last
         GT_RSI_DIP_LOOKBACK bars (excluding trigger).
      2. Trigger bar's RSI crosses UP through GT_RSI_CONFIRM_LEVEL.
      3. Recovery: bar[-2] RSI > the recent dip value (RSI is climbing
         back, not still falling into the trigger).

    Quality: 0.5 baseline; +0.5 scaled by how deep the dip was.
    SELL mirrors."""
    lookback = cfg.GT_RSI_DIP_LOOKBACK
    dip_level = cfg.GT_RSI_DIP_LEVEL
    confirm = cfg.GT_RSI_CONFIRM_LEVEL

    prev, curr = float(rsi_series.iloc[-2]), float(rsi_series.iloc[-1])

    if side == "BUY":
        if not (prev < confirm <= curr):
            return False, 0.0, 0.0
        dip_window = rsi_series.iloc[-(lookback + 1):-1]
        if dip_window.empty:
            return False, 0.0, 0.0
        dip_value = float(dip_window.min())
        if dip_value > dip_level:
            return False, 0.0, dip_value
        # Recovery: bar-before-trigger RSI > recent dip (some climb has happened).
        if prev <= dip_value:
            return False, 0.0, dip_value
        depth = max(0.0, dip_level - dip_value) / max(dip_level, 1e-6)
        return True, min(1.0, 0.5 + 0.5 * depth), dip_value

    # SELL mirror.
    inv_confirm = 100 - confirm
    inv_dip = 100 - dip_level
    if not (prev > inv_confirm >= curr):
        return False, 0.0, 0.0
    peak_window = rsi_series.iloc[-(lookback + 1):-1]
    if peak_window.empty:
        return False, 0.0, 0.0
    peak_value = float(peak_window.max())
    if peak_value < inv_dip:
        return False, 0.0, peak_value
    if prev >= peak_value:
        return False, 0.0, peak_value
    depth = max(0.0, peak_value - inv_dip) / max(100 - inv_dip, 1e-6)
    return True, min(1.0, 0.5 + 0.5 * depth), peak_value


# ─────────────────────────────────────────────────────────────────────
# Turtle band proximity
# ─────────────────────────────────────────────────────────────────────
def _turtle_proximity(df, band, side, atr_value):
    """Return (fires: bool, quality: 0..1). Quality = 1 when the extreme
    sits exactly on the band; 0 when it's at the loose-edge (proximity mult
    times ATR away)."""
    if atr_value <= 0:
        return False, 0.0
    tol = cfg.GT_PROXIMITY_ATR_MULT * atr_value
    if side == "BUY":
        extreme = min(df["l"].iloc[-1], df["l"].iloc[-2])
        distance = extreme - band  # positive means above the band
    else:
        extreme = max(df["h"].iloc[-1], df["h"].iloc[-2])
        distance = band - extreme
    if distance > tol:
        return False, 0.0
    # distance can be negative (wick pierced past band); clamp for quality.
    d = max(0.0, distance)
    return True, 1.0 - d / tol if tol > 0 else 1.0


# ─────────────────────────────────────────────────────────────────────
# ZLSMA slope classification
# ─────────────────────────────────────────────────────────────────────
def _zlsma_status(zlsma, atr_value, side):
    """aligned | flat | against."""
    slope = zlsma.iloc[-1] - zlsma.iloc[-cfg.GT_ZLSMA_SLOPE_LOOKBACK]
    flat_threshold = cfg.GT_ZLSMA_FLAT_ATR_FRAC * atr_value
    if abs(slope) < flat_threshold:
        return "flat"
    if side == "BUY":
        return "aligned" if slope > 0 else "against"
    return "aligned" if slope < 0 else "against"


# ─────────────────────────────────────────────────────────────────────
# Chop filter
# ─────────────────────────────────────────────────────────────────────
def _is_chop(df, atr_value):
    """True when the recent range is compressed into fewer than
    GT_CHOP_MIN_RANGE_ATR ATRs. That means the market is going sideways
    and any RSI midline cross is a coin flip."""
    if atr_value <= 0:
        return True
    window = df.iloc[-cfg.GT_CHOP_LOOKBACK:]
    if len(window) < cfg.GT_CHOP_LOOKBACK:
        return True
    range_ = float(window["h"].max() - window["l"].min())
    return (range_ / atr_value) < cfg.GT_CHOP_MIN_RANGE_ATR


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def find_golden_trio_candidate(candles):
    """Backwards-compatible wrapper: returns just the candidate dict (or None).
    Prefer find_golden_trio_candidate_diag() for per-gate diagnostics."""
    candidate, _reason = find_golden_trio_candidate_diag(candles)
    return candidate


def find_golden_trio_candidate_diag(candles):
    """Return (candidate_or_None, block_reason). block_reason is a short
    string naming the gate that killed every direction, or None on success."""
    warmup = max(
        cfg.GT_ZLSMA_PERIOD * 2,
        cfg.GT_TURTLE_PERIOD,
        cfg.GT_RSI_PERIOD + cfg.GT_RSI_DIP_LOOKBACK + cfg.GT_RSI_RISE_BARS + 2,
        cfg.GT_CHOP_LOOKBACK,
    )
    if not candles or len(candles) < warmup:
        return None, f"warmup ({len(candles) if candles else 0}/{warmup} bars)"

    df = pd.DataFrame(candles)
    close = df["c"]
    rsi_series = ind.rsi(close, cfg.GT_RSI_PERIOD)
    zlsma = ind.zero_lag_sma(close, cfg.GT_ZLSMA_PERIOD)
    upper, lower, _mid = ind.donchian_channels(df, cfg.GT_TURTLE_PERIOD)
    atr_series = ind.atr(df)

    if pd.isna(zlsma.iloc[-1]) or pd.isna(rsi_series.iloc[-1]):
        return None, "indicator NaN (needs more warmup)"
    if pd.isna(zlsma.iloc[-cfg.GT_ZLSMA_SLOPE_LOOKBACK]):
        return None, "ZLSMA slope window NaN"

    curr_close = float(close.iloc[-1])
    curr_open = float(df["o"].iloc[-1])
    curr_atr = float(atr_series.iloc[-1])
    curr_upper = float(upper.iloc[-1])
    curr_lower = float(lower.iloc[-1])

    # Global chop veto -- kills every direction, not per-side.
    if _is_chop(df, curr_atr):
        recent_range = float(df["h"].iloc[-cfg.GT_CHOP_LOOKBACK:].max() - df["l"].iloc[-cfg.GT_CHOP_LOOKBACK:].min())
        return None, f"chop veto (range {recent_range:.1f} < {cfg.GT_CHOP_MIN_RANGE_ATR}xATR={cfg.GT_CHOP_MIN_RANGE_ATR * curr_atr:.1f})"

    curr_high = float(df["h"].iloc[-1])
    curr_low = float(df["l"].iloc[-1])
    curr_range = max(curr_high - curr_low, 1e-9)

    per_side_reasons = []
    for side, band, opp_band in [("BUY", curr_lower, curr_upper), ("SELL", curr_upper, curr_lower)]:
        # Reject only a *decisively* counter-direction trigger bar. Dojis and
        # small counter-bodies at reversal pivots are normal -- rsi-seq +
        # turtle + zlsma already confirm direction.
        counter_body_ratio = abs(curr_close - curr_open) / curr_range
        if side == "BUY" and curr_close < curr_open and counter_body_ratio > cfg.GT_COUNTER_BODY_MAX_RATIO:
            per_side_reasons.append(f"{side}:strong-bearish-body")
            continue
        if side == "SELL" and curr_close > curr_open and counter_body_ratio > cfg.GT_COUNTER_BODY_MAX_RATIO:
            per_side_reasons.append(f"{side}:strong-bullish-body")
            continue

        # Sequenced RSI gate.
        fires, rsi_quality_frac, dip_value = _rsi_reversal_sequence(rsi_series, side)
        if not fires:
            per_side_reasons.append(f"{side}:rsi-seq")
            continue

        # Turtle band proximity gate.
        band_ok, turtle_quality_frac = _turtle_proximity(df, band, side, curr_atr)
        if not band_ok:
            per_side_reasons.append(f"{side}:turtle")
            continue

        # ZLSMA direction.
        zlsma_status = _zlsma_status(zlsma, curr_atr, side)
        if zlsma_status == "against":
            per_side_reasons.append(f"{side}:zlsma-against")
            continue

        # Build entry / SL / TPs.
        entry = curr_close
        if cfg.TARGET_MODE == "FIXED":
            pt = cfg.POINT_VALUE
            if side == "BUY":
                stop = entry - cfg.FIXED_SL_POINTS * pt
                tp1 = entry + cfg.FIXED_TP1_POINTS * pt
                tp2 = entry + cfg.FIXED_TP2_POINTS * pt
            else:
                stop = entry + cfg.FIXED_SL_POINTS * pt
                tp1 = entry - cfg.FIXED_TP1_POINTS * pt
                tp2 = entry - cfg.FIXED_TP2_POINTS * pt
            tp3 = tp2
            risk = abs(entry - stop)
        else:
            # Structural: SL just past the tested band + buffer; TPs scale
            # by distance to opposite band.
            if side == "BUY":
                stop = min(df["l"].iloc[-2:].min(), band) - cfg.GT_SL_BUFFER_ATR_MULT * curr_atr
                if stop >= entry:
                    continue
                tp3 = opp_band
                if tp3 <= entry:
                    continue
                reward = tp3 - entry
            else:
                stop = max(df["h"].iloc[-2:].max(), band) + cfg.GT_SL_BUFFER_ATR_MULT * curr_atr
                if stop <= entry:
                    continue
                tp3 = opp_band
                if tp3 >= entry:
                    continue
                reward = entry - tp3
            risk = abs(entry - stop)
            if reward < risk:
                continue
            tp1_dist = min(cfg.TP1_R_MULT * risk, reward / 3)
            tp2_dist = reward * 2 / 3
            if side == "BUY":
                tp1 = entry + tp1_dist
                tp2 = entry + tp2_dist
            else:
                tp1 = entry - tp1_dist
                tp2 = entry - tp2_dist

        rsi_quality_pts = round(rsi_quality_frac * cfg.SCORE_RSI_CONFIRM_MAX)
        turtle_quality_pts = round(turtle_quality_frac * cfg.SCORE_TURTLE_MAX)

        return {
            "pattern": PATTERN_NAME,
            "direction": side,
            "entry_price": float(entry),
            "stop_loss": float(stop),
            "tp1": float(tp1),
            "tp2": float(tp2),
            "tp3": float(tp3),
            "risk": float(risk),
            "rsi_quality": int(rsi_quality_pts),
            "turtle_quality": int(turtle_quality_pts),
            "zlsma_status": zlsma_status,
            "rsi": float(rsi_series.iloc[-1]),
            "zlsma": float(zlsma.iloc[-1]),
            "turtle_upper": float(curr_upper),
            "turtle_lower": float(curr_lower),
            "atr": float(curr_atr),
        }, None

    return None, " | ".join(per_side_reasons) if per_side_reasons else "unknown"
