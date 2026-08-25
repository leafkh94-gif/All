"""Golden Trio strategy — RSI hook-up at a Turtle band, ZLSMA direction
filter, chop-market rejection. The candidate this returns is scored
downstream in scoring_strategy.score_candidate.

Hard gates (return None if any fails):
  1. Not chop (recent range >= GT_CHOP_MIN_RANGE_ATR * ATR over last
     GT_CHOP_LOOKBACK bars).
  2. RSI hook-up (per side):
     BUY -> the trigger bar's RSI is climbing (curr > prev), has climbed
     at least GT_RSI_MIN_HOOK points above the local minimum in the last
     GT_RSI_DIP_LOOKBACK bars, and RSI is not below GT_RSI_BUY_FLOOR (to
     avoid catching a falling knife).
     SELL mirrors on the peak side (drop from local max, floor at
     100 - GT_RSI_BUY_FLOOR).
     Note: the original spec used an absolute-cross gate (RSI dipping
     below one level and crossing back above another). It was replaced
     with hook-up detection because the cross literally never fires on
     a trend-continuation session where RSI stays elevated all day.
  3. Turtle band tested: the trigger bar's extreme (low for BUY, high for
     SELL) sits within GT_PROXIMITY_ATR_MULT * ATR of the band. Uses a
     GT_TURTLE_PERIOD-bar Donchian; the shorter period keeps the band
     close enough to price that real pullbacks reach it.
  4. Trigger-bar body veto: block only decisively counter-direction
     candles (counter body > GT_COUNTER_BODY_MAX_RATIO of range).
     Dojis/small counter-bodies still pass -- the rsi+turtle+zlsma stack
     already confirms direction.
  5. ZLSMA slope not against the direction:
       against -> None (never fires)
       flat    -> allowed, scored as flat downstream (blocks A+)
       aligned -> full points downstream

Component quality scores handed to the scorer:
  - rsi_quality: 0..SCORE_RSI_CONFIRM_MAX from _rsi_reversal_sequence
    (scales with the size of the hook from the local low)
  - turtle_quality: 0..SCORE_TURTLE_MAX from how tightly the trigger
    bar's extreme hugged the band
  - zlsma_status: "aligned" | "flat"

Fixed target mode (cfg.TARGET_MODE == "FIXED") produces stop/TP1/TP2/TP3
at fixed point offsets (FIXED_SL_POINTS / FIXED_TP{1,2,3}_POINTS scaled
by POINT_VALUE). Structural mode uses Turtle-band-derived targets and
still fills TP3 as the opposite band.
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

    Hook-up detection (BUY): find the local RSI minimum inside the last
    GT_RSI_DIP_LOOKBACK bars; the trigger bar must be climbing (curr>prev)
    and the total climb from the local low must be at least GT_RSI_MIN_HOOK
    points. Rejects buys made while still deeply oversold via GT_RSI_BUY_FLOOR.

    Replaces the original absolute-cross gate (prev < 50 <= curr) which
    never fired during trend-continuation sessions where RSI stayed above
    50 the whole day. Any real momentum reversal now fires regardless of
    absolute level.

    SELL mirrors on the peak side.
    """
    lookback = cfg.GT_RSI_DIP_LOOKBACK
    hook = cfg.GT_RSI_MIN_HOOK

    if len(rsi_series) < lookback + 2:
        return False, 0.0, 0.0

    prev, curr = float(rsi_series.iloc[-2]), float(rsi_series.iloc[-1])
    prior = rsi_series.iloc[-(lookback + 1):-1]  # excludes trigger

    if side == "BUY":
        if curr <= prev:
            return False, 0.0, 0.0
        dip_value = float(prior.min())
        climb = curr - dip_value
        if climb < hook:
            return False, 0.0, dip_value
        if curr < cfg.GT_RSI_BUY_FLOOR:
            return False, 0.0, dip_value
        # Quality: 0.5 at exactly the hook threshold, 1.0 at hook+10.
        quality = min(1.0, 0.5 + (climb - hook) / 20.0)
        return True, max(0.0, quality), dip_value

    # SELL mirror.
    if curr >= prev:
        return False, 0.0, 0.0
    peak_value = float(prior.max())
    drop = peak_value - curr
    if drop < hook:
        return False, 0.0, peak_value
    if curr > (100 - cfg.GT_RSI_BUY_FLOOR):
        return False, 0.0, peak_value
    quality = min(1.0, 0.5 + (drop - hook) / 20.0)
    return True, max(0.0, quality), peak_value


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

    # Chop regime -- was a hard veto; now downgraded to a candidate tag
    # that scoring converts into a penalty. Reason: a real liquidity /
    # CHOCH / structure setup can still be worth an M15 alert even when
    # the last 20 bars have been rangebound. A+ is still blocked in
    # chop via score_candidate.aplus_eligible.
    chop_regime = _is_chop(df, curr_atr)

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

        # ZLSMA direction. Was a hard veto for "against"; now downgraded
        # to a candidate tag that scoring turns into a penalty. Reason
        # (user-directed): XAUUSD often reverses BEFORE a lagging trend
        # indicator flips, so rejecting all counter-ZLSMA setups killed
        # legitimate M15 opportunities. A+ still blocked on "against"
        # via score_candidate.aplus_eligible.
        zlsma_status = _zlsma_status(zlsma, curr_atr, side)

        # Build entry / SL / TPs.
        entry = curr_close
        if cfg.TARGET_MODE == "FIXED":
            pt = cfg.POINT_VALUE
            if side == "BUY":
                stop = entry - cfg.FIXED_SL_POINTS * pt
                tp1 = entry + cfg.FIXED_TP1_POINTS * pt
                tp2 = entry + cfg.FIXED_TP2_POINTS * pt
                tp3 = entry + cfg.FIXED_TP3_POINTS * pt
            else:
                stop = entry + cfg.FIXED_SL_POINTS * pt
                tp1 = entry - cfg.FIXED_TP1_POINTS * pt
                tp2 = entry - cfg.FIXED_TP2_POINTS * pt
                tp3 = entry - cfg.FIXED_TP3_POINTS * pt
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
            "chop_regime": bool(chop_regime),
            "rsi": float(rsi_series.iloc[-1]),
            "zlsma": float(zlsma.iloc[-1]),
            "turtle_upper": float(curr_upper),
            "turtle_lower": float(curr_lower),
            "atr": float(curr_atr),
        }, None

    return None, " | ".join(per_side_reasons) if per_side_reasons else "unknown"
