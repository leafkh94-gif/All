"""Golden Trio — RSI momentum evidence + Turtle location evidence,
with ZLSMA slope as a separate trend-alignment axis. The candidate this
returns is scored downstream in scoring_strategy.score_candidate.

ARCHITECTURE NOTE (this is the important part)
──────────────────────────────────────────────
RSI and Turtle are **evidence**, not permission. Neither answers "may
this setup exist?"; both answer "how good is it?". Concretely:

  - RSI must show *some* turn in the entry direction (curr > prev for a
    BUY) -- without that there is no reversal to speak of -- but the size
    of the hook only scales `rsi_quality`. There is no minimum hook, no
    absolute level to cross, and no oversold veto; being deeply oversold
    on a BUY down-weights the evidence to 0.4x instead of killing it.
  - Turtle proximity scales `turtle_quality` smoothly from touching the
    band down to ~0 at GT_PROXIMITY_ATR_HARD_VETO ATR away. The single
    remaining hard rejection is beyond that cap, where the setup is at
    the wrong end of the range entirely.
  - ZLSMA slope never rejects. It is classified aligned / flat / against
    and scored as a signed contribution downstream.
  - Chop never rejects. It is tagged on the candidate and scored as a
    penalty downstream.

The only conditions that still return None are structural: not enough
bars, NaN indicators, a decisively counter-direction trigger bar, no RSI
turn at all, and the Turtle hard cap.

Outputs consumed by the scorer:
  - setup_quality: 0..1, the weighted blend of RSI and Turtle evidence
    (GT_QUALITY_WEIGHT_RSI / GT_QUALITY_WEIGHT_TURTLE). This is the
    single number the scorer multiplies by SCORE_SETUP_MAX, and it is
    the axis SMC also reports on, so the two detectors are comparable by
    construction.
  - rsi_quality / turtle_quality: legacy per-component points, retained
    for the alert breakdown display only.
  - zlsma_status: "aligned" | "flat" | "against"
  - chop_regime: bool

Targets come from strategy.targets.build_targets, shared with SMC, in
either FIXED (dollar ladder) or ATR (volatility-scaled ladder) mode.
STRUCTURAL mode still derives targets from the Turtle bands here.
"""
import pandas as pd

import scoring_indicators as ind
import strategy_config as cfg
from strategy import targets

PATTERN_NAME = "GOLDEN_TRIO"


# ─────────────────────────────────────────────────────────────────────
# Sequenced RSI reversal gate
# ─────────────────────────────────────────────────────────────────────
def _rsi_reversal_sequence(rsi_series, side):
    """Return (fires: bool, quality: 0..1, extreme_value: float) describing
    the RSI-reversal quality on the trigger bar.

    Soft gate (was hard). RSI now contributes *evidence*, not a veto:

      - Direction check (curr > prev for BUY, curr < prev for SELL) is
        still required -- without any climb/drop we can't call it a
        reversal at all.
      - The old GT_RSI_MIN_HOOK hard threshold is gone. Quality scales
        smoothly with the climb/drop from the local extreme: 3pt hook =
        ~0.3, 8pt = ~0.6, 15pt+ = 1.0.
      - GT_RSI_BUY_FLOOR now down-weights instead of vetoing: still
        firing while deeply oversold (BUY: curr < 40) drops the quality
        multiplier to 0.4x so it's weak evidence rather than an outright
        buy-the-knife signal. SELL mirrors on 100 - floor.
    """
    lookback = cfg.GT_RSI_DIP_LOOKBACK

    if len(rsi_series) < lookback + 2:
        return False, 0.0, 0.0

    prev, curr = float(rsi_series.iloc[-2]), float(rsi_series.iloc[-1])
    prior = rsi_series.iloc[-(lookback + 1):-1]  # excludes trigger

    if side == "BUY":
        if curr <= prev:
            return False, 0.0, 0.0  # no climb -> no BUY reversal at all
        dip_value = float(prior.min())
        climb = curr - dip_value
        # Smooth quality curve: saturating at ~15pt climb.
        quality = min(1.0, climb / 15.0)
        if curr < cfg.GT_RSI_BUY_FLOOR:
            quality *= 0.4  # deeply oversold: weak evidence, still fires
        return True, max(0.0, quality), dip_value

    # SELL mirror.
    if curr >= prev:
        return False, 0.0, 0.0
    peak_value = float(prior.max())
    drop = peak_value - curr
    quality = min(1.0, drop / 15.0)
    if curr > (100 - cfg.GT_RSI_BUY_FLOOR):
        quality *= 0.4
    return True, max(0.0, quality), peak_value


# ─────────────────────────────────────────────────────────────────────
# Turtle band proximity
# ─────────────────────────────────────────────────────────────────────
def _turtle_proximity(df, band, side, atr_value):
    """Return (fires: bool, quality: 0..1).

    Soft gate (was hard). Turtle location now contributes evidence
    across a much wider distance range:

      - Touching / piercing the band  -> quality ~1.0 (best)
      - Within GT_PROXIMITY_ATR_MULT * ATR -> smooth 1.0 -> 0.5
      - Beyond GT_PROXIMITY_ATR_MULT and up to GT_PROXIMITY_ATR_HARD_VETO
        -> smooth 0.5 -> ~0.0 (fires but as very weak evidence)
      - Beyond GT_PROXIMITY_ATR_HARD_VETO -> hard veto (setup is at
        the wrong end of the range and would demand a huge move
        against the trend to work; almost never a real edge)

    Fires=False only in that last extreme case, so most setups now
    reach scoring and the ATR distance shows up as a quality signal
    rather than as a silent gate."""
    if atr_value <= 0:
        return False, 0.0
    tol = cfg.GT_PROXIMITY_ATR_MULT * atr_value
    hard = cfg.GT_PROXIMITY_ATR_HARD_VETO * atr_value
    if side == "BUY":
        extreme = min(df["l"].iloc[-1], df["l"].iloc[-2])
        distance = extreme - band     # positive means above lower band
    else:
        extreme = max(df["h"].iloc[-1], df["h"].iloc[-2])
        distance = band - extreme     # positive means below upper band
    if distance > hard:
        return False, 0.0
    d = max(0.0, distance)
    if d <= tol:
        # Inside the "good" zone: quality 1.0 (on band) -> 0.5 (at tol).
        quality = 1.0 - 0.5 * (d / tol)
    else:
        # Extended: quality 0.5 (at tol) -> ~0 (at hard cap).
        span = max(hard - tol, 1e-9)
        quality = 0.5 * (1.0 - (d - tol) / span)
    return True, max(0.0, min(1.0, quality))


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
# Shared market context
# ─────────────────────────────────────────────────────────────────────
def market_context(candles, direction):
    """ZLSMA alignment + chop regime for an arbitrary direction.

    Golden Trio computes these inline for its own candidate. SMC
    candidates need the *same* two axes measured the *same* way, or the
    two detectors' scores aren't comparable no matter how the quality
    budgets are normalised. Returns
    {"zlsma_status": ..., "chop_regime": ..., "atr": ...} or None when
    there aren't enough bars.
    """
    warmup = max(cfg.GT_ZLSMA_PERIOD * 2 + cfg.GT_ZLSMA_SLOPE_LOOKBACK,
                 cfg.GT_CHOP_LOOKBACK)
    if not candles or len(candles) < warmup:
        return None
    df = pd.DataFrame(candles)
    zlsma = ind.zero_lag_sma(df["c"], cfg.GT_ZLSMA_PERIOD)
    atr_value = float(ind.atr(df).iloc[-1])
    if pd.isna(zlsma.iloc[-1]) or pd.isna(zlsma.iloc[-cfg.GT_ZLSMA_SLOPE_LOOKBACK]):
        return None
    return {
        "zlsma_status": _zlsma_status(zlsma, atr_value, direction),
        "chop_regime": bool(_is_chop(df, atr_value)),
        "atr": atr_value,
        "zlsma": float(zlsma.iloc[-1]),
    }


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def find_golden_trio_candidate(candles, target_mode=None):
    """Backwards-compatible wrapper: returns just the candidate dict (or None).
    Prefer find_golden_trio_candidate_diag() for per-gate diagnostics."""
    candidate, _reason = find_golden_trio_candidate_diag(candles, target_mode=target_mode)
    return candidate


def find_golden_trio_candidate_diag(candles, target_mode=None):
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

        # ZLSMA direction -- a scored axis, never a veto. XAUUSD often
        # reverses BEFORE a lagging trend indicator flips, so rejecting
        # every counter-ZLSMA setup killed legitimate M15 opportunities.
        # "against" costs SCORE_ZLSMA_AGAINST points downstream, which is
        # enough to keep a weak setup below A+ without hiding a strong one.
        zlsma_status = _zlsma_status(zlsma, curr_atr, side)

        # Build entry / SL / TPs.
        # Enter on a limit a fraction of an ATR better than the trigger
        # close. Taking the close means buying the top of the bar that
        # produced the signal, which measured as an ~8-point win-rate
        # penalty on data with no directional information at all. See
        # ENTRY_PULLBACK_ATR in strategy_config.
        entry = curr_close - (1.0 if side == "BUY" else -1.0) * \
            cfg.ENTRY_PULLBACK_ATR * curr_atr
        if cfg.TARGET_MODE in ("FIXED", "ATR"):
            t = targets.build_targets(entry, side, atr_value=curr_atr,
                                      mode=target_mode or cfg.TARGET_MODE)
            if not t:
                per_side_reasons.append(f"{side}:no-targets")
                continue
            stop, tp1, tp2, tp3 = t["stop_loss"], t["tp1"], t["tp2"], t["tp3"]
            risk = t["risk"]
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

        # Unified 0..1 setup quality. This -- not the legacy point
        # ceilings -- is what the scorer multiplies by SCORE_SETUP_MAX,
        # so Golden Trio and SMC sit on one axis. RSI and Turtle are now
        # weighted *evidence* inside that fraction; neither is a gate on
        # whether the setup exists.
        setup_quality = (cfg.GT_QUALITY_WEIGHT_RSI * rsi_quality_frac
                         + cfg.GT_QUALITY_WEIGHT_TURTLE * turtle_quality_frac)

        return {
            "pattern": PATTERN_NAME,
            "direction": side,
            "setup_quality": float(max(0.0, min(1.0, setup_quality))),
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
