"""Golden Trio strategy — the sole signal source for the gold-only bot.

Combines three ingredients to filter chop:
  * Turtle Trade Channels (Donchian) — dynamic support/resistance
  * RSI oversold/overbought + confirmation cross — momentum reversal
  * Zero Lag SMA(50) — trend gate

Long fires when all four gates align:
  1. RSI dipped below GT_RSI_OVERSOLD in the last GT_RSI_OVERSOLD_LOOKBACK bars
  2. RSI crosses up through GT_RSI_CONFIRM_LEVEL on the trigger bar
  3. Close > ZLSMA(50)
  4. Close within GT_PROXIMITY_ATR_MULT * ATR of the lower Turtle band

Short mirrors on the opposite side (RSI>OVERBOUGHT, cross down through
100-CONFIRM_LEVEL, close<ZLSMA, close near upper Turtle band).

Returned candidate dict shape matches what score_candidate() consumes.
"""
import pandas as pd

import scoring_indicators as ind
import strategy_config as cfg

PATTERN_NAME = "GOLDEN_TRIO"


def _last_two(series):
    return series.iloc[-2], series.iloc[-1]


def _rsi_dipped_recently(rsi_series, threshold, lookback, side):
    window = rsi_series.iloc[-(lookback + 1):-1]  # excludes trigger bar itself
    if side == "BUY":
        return (window < threshold).any()
    return (window > threshold).any()


def _rsi_crosses_level(rsi_series, level, side):
    prev, curr = _last_two(rsi_series)
    if side == "BUY":
        return prev < level <= curr
    return prev > level >= curr


def _bar_tested_band(df, band, side, atr_value, mult, lookback=3):
    """True if any of the last `lookback` bars' extreme (low for BUY, high
    for SELL) came within `mult * atr` of the Turtle band. This is the
    "supported by / tested" condition -- a bullish reversal bar has a long
    lower wick that touches the band while its close pulls away."""
    if atr_value <= 0:
        return False
    if side == "BUY":
        recent_low = df["l"].iloc[-lookback:].min()
        return recent_low - band <= mult * atr_value
    recent_high = df["h"].iloc[-lookback:].max()
    return band - recent_high <= mult * atr_value


def _swing_low(df, lookback):
    return df["l"].iloc[-lookback:].min()


def _swing_high(df, lookback):
    return df["h"].iloc[-lookback:].max()


# Stop is placed just beyond the trigger bar's wick -- the bar itself carries
# the long lower/upper wick that tested the Turtle band, so its extreme is the
# right anchor. Widening the window past ~2 bars pulls stop back into the
# pullback body, ballooning risk with no protective benefit.
GT_SL_SWING_LOOKBACK = 2


def find_golden_trio_candidate(candles):
    """Return one candidate dict or None. `candles` is a list of dicts with
    keys o/h/l/c (the standard project candle shape)."""
    if not candles or len(candles) < max(
        cfg.GT_ZLSMA_PERIOD * 2,  # ZLSMA is an SMA-of-SMA so needs 2x lookback
        cfg.GT_TURTLE_PERIOD,
        cfg.GT_RSI_PERIOD + cfg.GT_RSI_OVERSOLD_LOOKBACK + 2,
    ):
        return None

    df = pd.DataFrame(candles)
    close = df["c"]
    rsi_series = ind.rsi(close, cfg.GT_RSI_PERIOD)
    zlsma = ind.zero_lag_sma(close, cfg.GT_ZLSMA_PERIOD)
    upper, lower, _mid = ind.donchian_channels(df, cfg.GT_TURTLE_PERIOD)
    atr_series = ind.atr(df)

    if pd.isna(zlsma.iloc[-1]) or pd.isna(rsi_series.iloc[-1]) or pd.isna(zlsma.iloc[-cfg.GT_ZLSMA_SLOPE_LOOKBACK]):
        return None

    curr_close = close.iloc[-1]
    curr_zlsma = zlsma.iloc[-1]
    curr_upper = upper.iloc[-1]
    curr_lower = lower.iloc[-1]
    curr_atr = atr_series.iloc[-1]
    curr_rsi = rsi_series.iloc[-1]

    for side, band, opp_band in [("BUY", curr_lower, curr_upper), ("SELL", curr_upper, curr_lower)]:
        dipped = _rsi_dipped_recently(
            rsi_series,
            cfg.GT_RSI_OVERSOLD if side == "BUY" else cfg.GT_RSI_OVERBOUGHT,
            cfg.GT_RSI_OVERSOLD_LOOKBACK,
            side,
        )
        if not dipped:
            continue

        confirm_level = cfg.GT_RSI_CONFIRM_LEVEL if side == "BUY" else 100 - cfg.GT_RSI_CONFIRM_LEVEL
        if not _rsi_crosses_level(rsi_series, confirm_level, side):
            continue

        # ZLSMA trend-context gate. Strict "close above ZLSMA now" would veto
        # every reversal (a bar bouncing off the lower band is by definition
        # below a slow MA); strict "slope up" catches the pullback that
        # preceded the reversal. Compromise: require that in the last
        # GT_ZLSMA_SLOPE_LOOKBACK bars, price spent at least one bar on the
        # trend side of ZLSMA -- i.e. we're reversing back into an established
        # trend, not counter-trend into fresh territory.
        window = df["c"].iloc[-cfg.GT_ZLSMA_SLOPE_LOOKBACK:]
        zlsma_window = zlsma.iloc[-cfg.GT_ZLSMA_SLOPE_LOOKBACK:]
        if side == "BUY" and not (window > zlsma_window).any():
            continue
        if side == "SELL" and not (window < zlsma_window).any():
            continue

        if not _bar_tested_band(df, band, side, curr_atr, cfg.GT_PROXIMITY_ATR_MULT):
            continue

        entry = curr_close
        if cfg.TARGET_MODE == "FIXED":
            # Small-scalp fixed offsets. Bypasses Turtle-band structural
            # targets; TP3 collapses to TP2 so the 3-tier tracker still works.
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
            if side == "BUY":
                raw_stop = _swing_low(df, GT_SL_SWING_LOOKBACK)
                stop = raw_stop - cfg.GT_SL_BUFFER_ATR_MULT * curr_atr
                if stop >= entry:
                    continue
                risk = entry - stop
                tp3 = opp_band
                if tp3 <= entry:
                    continue
                reward = tp3 - entry
            else:
                raw_stop = _swing_high(df, GT_SL_SWING_LOOKBACK)
                stop = raw_stop + cfg.GT_SL_BUFFER_ATR_MULT * curr_atr
                if stop <= entry:
                    continue
                risk = stop - entry
                tp3 = opp_band
                if tp3 >= entry:
                    continue
                reward = entry - tp3

            # Reject sub-1R setups outright -- the whole point of this
            # structural mode is bouncing all the way to the opposite band.
            if reward < risk:
                continue

            # TP1 = min(1R, 33% of way to opposite band); TP2 = 66%; TP3 = band.
            tp1_dist = min(cfg.TP1_R_MULT * risk, reward / 3)
            tp2_dist = reward * 2 / 3
            if side == "BUY":
                tp1 = entry + tp1_dist
                tp2 = entry + tp2_dist
            else:
                tp1 = entry - tp1_dist
                tp2 = entry - tp2_dist

        # Quality score components used by score_candidate():
        #   * up to 20 for RSI extremity distance from confirm level
        #   * up to 20 for tight proximity to the tested Turtle band
        rsi_dist = abs(curr_rsi - confirm_level)
        extremity_pts = min(20, round(rsi_dist / 20 * 20))
        wick_extreme = df["l"].iloc[-3:].min() if side == "BUY" else df["h"].iloc[-3:].max()
        wick_dist = abs(wick_extreme - band) / (cfg.GT_PROXIMITY_ATR_MULT * curr_atr)
        proximity_pts = max(0, min(20, round((1 - wick_dist) * 20)))
        quality = extremity_pts + proximity_pts

        return {
            "pattern": PATTERN_NAME,
            "direction": side,
            "entry_price": float(entry),
            "stop_loss": float(stop),
            "tp1": float(tp1),
            "tp2": float(tp2),
            "tp3": float(tp3),
            "risk": float(risk),
            "quality": int(quality),
            "quality_max": cfg.GT_QUALITY_MAX,
            "rsi": float(curr_rsi),
            "zlsma": float(curr_zlsma),
            "turtle_upper": float(curr_upper),
            "turtle_lower": float(curr_lower),
            "atr": float(curr_atr),
        }

    return None
