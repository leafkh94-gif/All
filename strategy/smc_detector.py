"""
SMC-powered pattern detection using the community smartmoneyconcepts library.

Adds two new pattern types that the original 5 detectors don't cover:
  - ORDER_BLOCK — the institutional footprint candle before a BOS
  - CHOCH_REVERSAL — Change of Character (first break against structure)

Also provides improved swing detection with alternating-elimination,
which produces cleaner market structure than the raw fractal approach.
"""
import pandas as pd
import numpy as np

try:
    from smartmoneyconcepts import smc
    SMC_AVAILABLE = True
except ImportError as _smc_err:
    SMC_AVAILABLE = False
    _SMC_IMPORT_ERROR = _smc_err
else:
    _SMC_IMPORT_ERROR = None


def assert_smc_available():
    """Startup hard-fail. Half the signal engine is SMC; if the library is
    missing every SMC detector silently returns None and we ship gold
    alerts on Golden Trio alone without any warning. Call this once at
    process start (e.g. from main_alerts / run_forever)."""
    if not SMC_AVAILABLE:
        raise RuntimeError(
            "smartmoneyconcepts is required for the SMC detectors "
            "(Order Block / CHOCH / Liquidity Sweep) but failed to import: "
            f"{_SMC_IMPORT_ERROR}. Install it (`pip install smartmoneyconcepts`) "
            "or remove SMC from find_candidate."
        )

import strategy_config as cfg


def _to_smc_df(candles):
    """Convert our candle dicts to the OHLCV DataFrame the library expects.
    Volume is filled to 1.0 wherever it's missing / None / NaN --
    smartmoneyconcepts.ob() crashes on `None + None` during obVolume
    accumulation, and Capital.com's feed frequently returns v=None."""
    df = pd.DataFrame(candles)
    out = pd.DataFrame({
        "open": df["o"],
        "high": df["h"],
        "low": df["l"],
        "close": df["c"],
    })
    if "v" in df.columns:
        vol = pd.to_numeric(df["v"], errors="coerce").fillna(1.0)
    else:
        vol = pd.Series([1.0] * len(df), index=df.index)
    out["volume"] = vol
    out.index = range(len(out))
    return out


def _atr(ohlc, period=14):
    """Simple ATR on an OHLC DataFrame."""
    tr = pd.concat([
        ohlc["high"] - ohlc["low"],
        (ohlc["high"] - ohlc["close"].shift(1)).abs(),
        (ohlc["low"] - ohlc["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def detect_order_block(candles, swing_length=None):
    """Detect unmitigated order blocks using the SMC library.

    An order block is the candle before a break of structure — where
    institutional orders clustered. Returns the most recent unmitigated
    OB as a candidate dict, or None.

    swing_length controls the swing detection sensitivity. Smaller = more
    responsive (good for M15), larger = fewer but stronger swings.
    """
    if not SMC_AVAILABLE or len(candles) < 30:
        return None

    ohlc = _to_smc_df(candles)
    swing_length = swing_length or cfg.SMC_SWING_LENGTH
    swing_hl = smc.swing_highs_lows(ohlc, swing_length=swing_length)
    ob_data = smc.ob(ohlc, swing_hl, close_mitigation=True)

    atr = _atr(ohlc)
    current_atr = atr.iloc[-1]
    if pd.isna(current_atr) or current_atr <= 0:
        return None

    last_price = ohlc["close"].iloc[-1]
    best = None

    for i in range(len(ob_data) - 1, -1, -1):
        ob_val = ob_data["OB"].iloc[i]
        if pd.isna(ob_val):
            continue

        mitigated = ob_data["MitigatedIndex"].iloc[i]
        if not pd.isna(mitigated):
            continue

        ob_top = ob_data["Top"].iloc[i]
        ob_bottom = ob_data["Bottom"].iloc[i]
        ob_pct = ob_data["Percentage"].iloc[i]
        is_bullish = int(ob_val) == 1

        if is_bullish:
            if last_price > ob_top:
                continue
            if last_price < ob_bottom - 2 * current_atr:
                continue
        else:
            if last_price < ob_bottom:
                continue
            if last_price > ob_top + 2 * current_atr:
                continue

        distance = abs(last_price - (ob_top + ob_bottom) / 2) / current_atr
        if distance > cfg.SMC_OB_MAX_DISTANCE_ATR:
            continue

        strength = ob_pct if not pd.isna(ob_pct) else 50.0
        ob_range = ob_top - ob_bottom
        displacement = ob_range / current_atr
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX,
                          22 + min(8, strength / 100 * 8) + min(8, displacement * 4)))

        direction = "BUY" if is_bullish else "SELL"
        candidate = {
            "pattern": "ORDER_BLOCK",
            "direction": direction,
            "sweep_price": float(ob_bottom if is_bullish else ob_top),
            "leg_extreme": float(ob_bottom if is_bullish else ob_top),
            "quality": quality,
            "ob_top": float(ob_top),
            "ob_bottom": float(ob_bottom),
            "ob_strength": float(strength),
        }

        if best is None or quality > best["quality"]:
            best = candidate

    return best


def detect_choch_reversal(candles, swing_length=None):
    """Detect Change of Character — the FIRST break against the prevailing
    market structure. More significant than a regular BOS because it signals
    a potential trend reversal.

    Returns the most recent CHOCH as a candidate dict, or None.
    """
    if not SMC_AVAILABLE or len(candles) < 30:
        return None

    ohlc = _to_smc_df(candles)
    swing_length = swing_length or cfg.SMC_SWING_LENGTH
    swing_hl = smc.swing_highs_lows(ohlc, swing_length=swing_length)
    bos_choch = smc.bos_choch(ohlc, swing_hl, close_break=True)

    atr = _atr(ohlc)
    current_atr = atr.iloc[-1]
    if pd.isna(current_atr) or current_atr <= 0:
        return None

    for i in range(len(bos_choch) - 1, -1, -1):
        choch_val = bos_choch["CHOCH"].iloc[i]
        if pd.isna(choch_val):
            continue

        broken_idx = bos_choch["BrokenIndex"].iloc[i]
        if pd.isna(broken_idx):
            continue

        recency = len(ohlc) - 1 - int(broken_idx)
        if recency > cfg.SMC_CHOCH_MAX_RECENCY:
            continue

        level = bos_choch["Level"].iloc[i]
        if pd.isna(level):
            continue

        is_bullish = int(choch_val) == 1
        last_price = ohlc["close"].iloc[-1]
        distance = abs(last_price - level) / current_atr

        if distance > cfg.SMC_OB_MAX_DISTANCE_ATR:
            continue

        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX,
                          26 + min(12, max(0, (cfg.SMC_CHOCH_MAX_RECENCY - recency)) * 2)))

        return {
            "pattern": "CHOCH_REVERSAL",
            "direction": "BUY" if is_bullish else "SELL",
            "sweep_price": float(level),
            "leg_extreme": float(level),
            "quality": quality,
            "choch_level": float(level),
            "broken_index": int(broken_idx),
        }

    return None


def detect_smc_liquidity_sweep(candles, swing_length=None):
    """Enhanced liquidity sweep using the SMC library's grouped-swing
    liquidity detection. Finds clusters of equal highs/lows (resting
    orders) that have been swept — more reliable than our original
    single-swing approach."""
    if not SMC_AVAILABLE or len(candles) < 30:
        return None

    ohlc = _to_smc_df(candles)
    swing_length = swing_length or cfg.SMC_SWING_LENGTH
    swing_hl = smc.swing_highs_lows(ohlc, swing_length=swing_length)
    liq = smc.liquidity(ohlc, swing_hl, range_percent=cfg.SMC_LIQUIDITY_RANGE_PCT)

    atr = _atr(ohlc)
    current_atr = atr.iloc[-1]
    if pd.isna(current_atr) or current_atr <= 0:
        return None

    last_idx = len(ohlc) - 1
    last_close = ohlc["close"].iloc[-1]

    for i in range(len(liq) - 1, -1, -1):
        liq_val = liq["Liquidity"].iloc[i]
        if pd.isna(liq_val):
            continue

        swept = liq["Swept"].iloc[i]
        if pd.isna(swept):
            continue

        if abs(last_idx - int(swept)) > cfg.SMC_SWEEP_RECENCY:
            continue

        level = liq["Level"].iloc[i]
        is_bullish_liq = int(liq_val) == 1

        if is_bullish_liq:
            if last_close >= level:
                continue
            direction = "SELL"
        else:
            if last_close <= level:
                continue
            direction = "BUY"

        depth = abs(last_close - level) / current_atr
        quality = int(min(cfg.PATTERN_QUALITY_BASE_MAX,
                          24 + min(14, depth * 10)))

        return {
            "pattern": "SMC_LIQUIDITY_SWEEP",
            "direction": direction,
            "sweep_price": float(level),
            "leg_extreme": float(level),
            "quality": quality,
        }

    return None


SMC_DETECTORS = [
    detect_order_block,
    detect_choch_reversal,
    detect_smc_liquidity_sweep,
]


def find_smc_candidate(entry_candles):
    """Run all SMC detectors and return the highest-quality match."""
    if not SMC_AVAILABLE:
        return None
    best = None
    for detector in SMC_DETECTORS:
        result = detector(entry_candles)
        if result and (best is None or result["quality"] > best["quality"]):
            best = result
    return best
