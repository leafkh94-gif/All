"""
Multi-timeframe confirmation layer.

The architecture this implements:

        M15 SETUP  (Golden Trio / SMC — creates the opportunity)
             │
    ┌────────┼────────┬────────┐
   M5       H1       H4       M1
confirm   context   regime   timing
    └────────┴────────┴────────┘
             │
           SCORE

Design rules that make this layer honest:

1. **Modifiers, not vetoes.** M15 creates the opportunity; every other
   timeframe can only move the score up or down. None of them can
   silently delete a setup. The one exception is nothing at all — there
   is no veto path in this module.

2. **Zero-centred.** Each timeframe returns a signed contribution:
   positive when it confirms the M15 direction, negative when it
   contradicts, 0 when it is neutral *or when its candles are not
   available*. Absence is therefore identical to neutrality, which is
   what lets an M15-only backtest be compared against live scoring
   without a systematic offset. `available=False` is recorded in the
   detail dict so a run can report exactly which timeframes it saw.

3. **Bounded.** Each contribution is clamped to its configured budget
   (cfg.SCORE_M5_MAX / SCORE_H1_MAX / SCORE_H4_MAX / SCORE_M1_MAX), so
   the sum of the layer is bounded and the total score stays on a 0..100
   scale.

Every read is of *closed* candles only — the caller is responsible for
never handing this module a developing bar (build_market and the
backtester's aggregate_htf both drop partial bars).
"""
import pandas as pd

import scoring_indicators as ind
import strategy_config as cfg


def _df(candles):
    if isinstance(candles, pd.DataFrame):
        return candles
    return pd.DataFrame(candles)


def _signed(direction, bullish_strength):
    """Map a -1..+1 bullish strength onto a -1..+1 agreement with `direction`."""
    return bullish_strength if direction == "BUY" else -bullish_strength


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _neutral(name, reason="unavailable"):
    return {"timeframe": name, "points": 0, "agreement": 0.0,
            "available": False, "detail": reason}


# ─────────────────────────────────────────────────────────────────────
# H4 — regime
# ─────────────────────────────────────────────────────────────────────
def h4_regime(candles_h4, direction, flat_band_pct=cfg.MTF_H4_FLAT_BAND_PCT):
    """Trend regime from EMA(20) slope over 5 H4 bars.

    Returns a contribution dict plus a `bias` string ("BULL"/"BEAR"/"FLAT")
    that the rest of the system still displays. Slope magnitude scales the
    points, so a barely-there trend contributes barely-any points instead
    of the old all-or-nothing ±15.
    """
    if not candles_h4 or len(candles_h4) < 30:
        out = _neutral("h4")
        out["bias"] = "FLAT"
        return out
    df = _df(candles_h4)
    e = ind.ema(df["c"], cfg.MTF_H4_EMA_PERIOD)
    if len(e) < 6 or pd.isna(e.iloc[-6]) or e.iloc[-6] == 0:
        out = _neutral("h4", "ema warmup")
        out["bias"] = "FLAT"
        return out

    change = float((e.iloc[-1] - e.iloc[-6]) / e.iloc[-6])
    if change > flat_band_pct:
        bias = "BULL"
    elif change < -flat_band_pct:
        bias = "BEAR"
    else:
        bias = "FLAT"

    # Strength saturates at MTF_H4_FULL_SLOPE_PCT of EMA movement.
    strength = _clamp(change / cfg.MTF_H4_FULL_SLOPE_PCT, -1.0, 1.0)
    if bias == "FLAT":
        strength = 0.0
    agreement = _signed(direction, strength)
    points = int(round(agreement * cfg.SCORE_H4_MAX))
    return {"timeframe": "h4", "points": points, "agreement": round(agreement, 3),
            "available": True, "bias": bias,
            "detail": f"ema20 slope {change*100:+.2f}% ({bias})"}


# ─────────────────────────────────────────────────────────────────────
# H1 — context
# ─────────────────────────────────────────────────────────────────────
def h1_context(candles_h1, direction):
    """Where price sits inside the recent H1 range, plus H1 EMA slope.

    Two independent readings, averaged:
      - Location: for a BUY, sitting in the lower half of the last
        MTF_H1_RANGE_BARS H1 bars is favourable (room to run, not buying
        the top). Mirrored for SELL.
      - Momentum: EMA(MTF_H1_EMA_PERIOD) slope over MTF_H1_SLOPE_BARS bars.

    Location is deliberately mean-reverting while momentum is
    trend-following; averaging them means H1 only strongly confirms when
    price has both room and a supporting drift, and only strongly
    contradicts when it has neither.
    """
    if not candles_h1 or len(candles_h1) < max(cfg.MTF_H1_RANGE_BARS,
                                               cfg.MTF_H1_EMA_PERIOD + cfg.MTF_H1_SLOPE_BARS):
        return _neutral("h1")
    df = _df(candles_h1)

    window = df.iloc[-cfg.MTF_H1_RANGE_BARS:]
    hi = float(window["h"].max())
    lo = float(window["l"].min())
    span = hi - lo
    last = float(df["c"].iloc[-1])
    if span <= 0:
        location = 0.0
    else:
        pos = (last - lo) / span             # 0 = at range low, 1 = at range high
        # A BUY wants room above it, so sitting near the range low is the
        # favourable case: +1 at the low, -1 at the high. SELL mirrors.
        bullish_location = 1.0 - 2.0 * pos
        location = bullish_location if direction == "BUY" else -bullish_location

    e = ind.ema(df["c"], cfg.MTF_H1_EMA_PERIOD)
    idx = -1 - cfg.MTF_H1_SLOPE_BARS
    if len(e) < abs(idx) or pd.isna(e.iloc[idx]) or e.iloc[idx] == 0:
        momentum = 0.0
        slope_pct = 0.0
    else:
        slope_pct = float((e.iloc[-1] - e.iloc[idx]) / e.iloc[idx])
        momentum = _signed(direction, _clamp(slope_pct / cfg.MTF_H1_FULL_SLOPE_PCT, -1.0, 1.0))

    agreement = _clamp((location + momentum) / 2.0, -1.0, 1.0)
    points = int(round(agreement * cfg.SCORE_H1_MAX))
    return {"timeframe": "h1", "points": points, "agreement": round(agreement, 3),
            "available": True,
            "detail": f"loc {location:+.2f} / mom {momentum:+.2f} (ema slope {slope_pct*100:+.2f}%)"}


# ─────────────────────────────────────────────────────────────────────
# M5 — confirmation
# ─────────────────────────────────────────────────────────────────────
def m5_confirmation(candles_m5, direction):
    """Has the lower timeframe actually turned in the setup's direction?

    Three cheap, independent reads, averaged:
      - Net displacement over the last MTF_M5_LOOKBACK bars, in ATR.
      - RSI slope over the same window (momentum turning, not level).
      - Share of closes in the setup direction (participation).

    This is the layer the old design was missing entirely: an M15 hook
    with no M5 follow-through is a different trade from one where the
    lower timeframe has already turned.
    """
    n = cfg.MTF_M5_LOOKBACK
    if not candles_m5 or len(candles_m5) < max(n + 2, cfg.MTF_M5_RSI_PERIOD + n + 2):
        return _neutral("m5")
    df = _df(candles_m5)

    atr_series = ind.atr(df)
    atr_v = float(atr_series.iloc[-1])
    if atr_v <= 0:
        return _neutral("m5", "zero atr")

    disp = float(df["c"].iloc[-1] - df["c"].iloc[-1 - n])
    displacement = _signed(direction, _clamp(disp / (atr_v * cfg.MTF_M5_FULL_DISP_ATR), -1.0, 1.0))

    r = ind.rsi(df["c"], cfg.MTF_M5_RSI_PERIOD)
    if pd.isna(r.iloc[-1]) or pd.isna(r.iloc[-1 - n]):
        rsi_slope = 0.0
    else:
        delta = float(r.iloc[-1] - r.iloc[-1 - n])
        rsi_slope = _signed(direction, _clamp(delta / cfg.MTF_M5_FULL_RSI_DELTA, -1.0, 1.0))

    body = df["c"].iloc[-n:] - df["o"].iloc[-n:]
    if direction == "BUY":
        share = float((body > 0).sum()) / n
    else:
        share = float((body < 0).sum()) / n
    participation = _clamp((share - 0.5) * 2.0, -1.0, 1.0)

    agreement = _clamp((displacement + rsi_slope + participation) / 3.0, -1.0, 1.0)
    points = int(round(agreement * cfg.SCORE_M5_MAX))
    return {"timeframe": "m5", "points": points, "agreement": round(agreement, 3),
            "available": True,
            "detail": (f"disp {displacement:+.2f} / rsi {rsi_slope:+.2f} / "
                       f"part {participation:+.2f}")}


# ─────────────────────────────────────────────────────────────────────
# M1 — entry timing only
# ─────────────────────────────────────────────────────────────────────
def m1_timing(candles_m1, direction, entry_price=None):
    """Entry timing, and nothing else.

    M1 has no business deciding whether a setup exists — it only answers
    "is this a good instant to be filled?". Two reads:
      - Immediate pressure: the last MTF_M1_LOOKBACK closes' direction.
      - Extension: how far price has already run from the intended entry
        in ATR terms. Chasing an entry that has already moved
        MTF_M1_FULL_EXTENSION_ATR against the fill is penalised.

    Budget is deliberately the smallest of the four (SCORE_M1_MAX).
    """
    n = cfg.MTF_M1_LOOKBACK
    if not candles_m1 or len(candles_m1) < n + 2:
        return _neutral("m1")
    df = _df(candles_m1)

    body = df["c"].iloc[-n:] - df["o"].iloc[-n:]
    if direction == "BUY":
        share = float((body > 0).sum()) / n
    else:
        share = float((body < 0).sum()) / n
    pressure = _clamp((share - 0.5) * 2.0, -1.0, 1.0)

    extension = 0.0
    atr_v = float(ind.atr(df).iloc[-1])
    if entry_price is not None and atr_v > 0:
        last = float(df["c"].iloc[-1])
        # Positive `run` = price has already moved in the trade's favour,
        # i.e. we would be chasing. That is a timing negative.
        run = (last - entry_price) if direction == "BUY" else (entry_price - last)
        extension = -_clamp(max(0.0, run) / (atr_v * cfg.MTF_M1_FULL_EXTENSION_ATR), 0.0, 1.0)

    agreement = _clamp((pressure + extension) / 2.0, -1.0, 1.0)
    points = int(round(agreement * cfg.SCORE_M1_MAX))
    return {"timeframe": "m1", "points": points, "agreement": round(agreement, 3),
            "available": True,
            "detail": f"pressure {pressure:+.2f} / extension {extension:+.2f}"}


# ─────────────────────────────────────────────────────────────────────
# Aggregate
# ─────────────────────────────────────────────────────────────────────
def evaluate(market, direction, entry_price=None):
    """Run every timeframe and return (total_points, breakdown_rows, detail).

    breakdown_rows are (tag, points) tuples ready to append to the score
    breakdown. detail is the per-timeframe dict, kept for diagnostics and
    for the backtest audit log.
    """
    h4 = h4_regime(market.get("h4") or [], direction)
    h1 = h1_context(market.get("h1") or [], direction)
    m5 = m5_confirmation(market.get("m5") or [], direction)
    m1 = m1_timing(market.get("m1") or [], direction, entry_price=entry_price)

    detail = {"h4": h4, "h1": h1, "m5": m5, "m1": m1}
    rows = []
    total = 0
    for tf in (h4, h1, m5, m1):
        total += tf["points"]
        if tf["points"]:
            sign = "confirm" if tf["points"] > 0 else "against"
            rows.append((f"{tf['timeframe']}_{sign}", tf["points"]))
        elif tf["available"]:
            rows.append((f"{tf['timeframe']}_neutral", 0))
    return total, rows, detail


def availability(detail):
    """Comma-joined list of timeframes that actually contributed data."""
    return ",".join(tf for tf, d in detail.items() if d.get("available"))
