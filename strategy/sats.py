"""
SATS — Self-Aware Trend System (Python port of the TradingView/Pine v1.13.1
indicator by WillyAlgoTrader), adapted to this bot's stateless per-window
detector contract.

WHAT THIS IS
────────────
An adaptive SuperTrend trend-follower. A SuperTrend (ATR trailing band) is
the core; a continuous Trend Quality Index (TQI) modulates the band width
and the flip logic each bar:

  - TQI (0..1) is a weighted blend of four factors measured every bar:
      efficiency (Kaufman ER), a volatility-regime factor (ATR regime or
      volume activity), structure (price position within its range), and
      momentum persistence (fraction of recent bars aligned with the move).
  - Band width is modulated non-linearly by TQI: clean trends compress the
    bands, chop widens them, via a power curve.
  - Asymmetric bands tighten the active (trailing) side and widen the
    passive side as trend quality rises.
  - Character-flip: the trend can flip on a TQI collapse (high -> low within
    a lookback window, price already moving against the trend) even when
    price has not broken the band.
  - Each confirmed flip produces a trade plan: entry at the flip close, a
    pivot-anchored stop with an ATR buffer and a hard max-distance cap, and
    TP1/TP2/TP3 at R-multiples (optionally scaled by TQI + volatility).

PORTING NOTES
─────────────
The Pine script is stateful across all history (it ratchets bands, EMA-
smooths multipliers, tracks trend age bar by bar). This bot calls detectors
statelessly with a fixed window of recent candles (cfg.MTF_FETCH_BARS), the
same window live and in backtest. So this port recomputes the whole
SuperTrend over the supplied window each call and reports a flip only on the
last (just-closed) bar. Within a window long enough to contain the current
trend segment the ratchet converges to the same state, and — critically —
live and backtest see the identical window, so they cannot diverge.

Everything Pine-specific that does not affect the *strategy* (dashboard,
watermark, on-chart labels, the webhook v2 schema, the two alertcondition
grade ladders) is intentionally not ported — this bot has its own alert
formatting, cooldown, tiering and backtester. The scoring pipeline grades a
SATS signal by its TQI: score = round(100 * TQI), so the existing tier,
calibration and walk-forward tooling keep working unchanged.

This module is pure and deterministic: given a candle window it returns a
signal dict or None. No I/O, no globals.
"""
import pandas as pd

import scoring_indicators as ind
import strategy_config as cfg

PATTERN_NAME = "SATS"

# Band-geometry constants (Pine: fixed, not user inputs).
_TQI_MULT_FLOOR = 0.6    # band width at perfect quality (TQI = 1)
_TQI_MULT_RANGE = 0.8    # added width at worst quality (TQI = 0)
_ASYM_TIGHTEN_MAX = 0.3  # max tightening of the active side at TQI = 1
_ASYM_WIDEN_MAX = 0.4    # max widening of the passive side at TQI = 1
_MULT_SMOOTH_ALPHA = 0.15


# ─────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _safe_div(num, den, fallback=0.0):
    if den == 0 or den is None or num is None:
        return fallback
    try:
        if pd.isna(num) or pd.isna(den):
            return fallback
    except TypeError:
        pass
    return num / den


def _map_clamp(v, in_lo, in_hi, out_lo, out_hi):
    t = _clamp(_safe_div(v - in_lo, in_hi - in_lo, 0.0), 0.0, 1.0)
    return out_lo + t * (out_hi - out_lo)


def _efficiency_ratio(close, i, length):
    """Kaufman ER at bar i over `length` bars: directed move / total path."""
    if i < length:
        return 0.0
    change = abs(close[i] - close[i - length])
    volatility = 0.0
    for k in range(i - length + 1, i + 1):
        volatility += abs(close[k] - close[k - 1])
    return _safe_div(change, volatility, 0.0)


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────
def evaluate(candles):
    """Return a SATS signal dict for a confirmed flip on the LAST candle of
    `candles`, or None. `candles` is a list of dicts with o/h/l/c (v
    optional), chronological. The last element is the just-closed bar.

    The returned dict already carries a complete trade plan
    (entry/stop_loss/tp1/tp2/tp3/risk), so score_candidate does not attach
    targets for SATS.
    """
    sig, _reason = evaluate_diag(candles)
    return sig


def evaluate_diag(candles):
    """(signal_or_None, reason_str). reason names why no flip fired, for
    diagnostics; None on a fired signal."""
    atr_len = cfg.SATS_ATR_LEN
    er_len = cfg.SATS_ER_LEN
    struct_len = cfg.SATS_STRUCT_LEN
    mom_len = cfg.SATS_MOM_LEN
    baseline_len = cfg.SATS_ATR_BASELINE_LEN

    warmup = max(atr_len + 2, er_len + 1, struct_len + 1, mom_len + 1,
                 cfg.SATS_CHARFLIP_MIN_AGE + 3)
    n = len(candles) if candles else 0
    if n < warmup:
        return None, f"warmup ({n}/{warmup} bars)"

    df = pd.DataFrame(candles)
    close = df["c"].astype(float).tolist()
    high = df["h"].astype(float).tolist()
    low = df["l"].astype(float).tolist()
    src = close  # Pine default source = close

    # ── ATR + effective ATR (vectorised) ────────────────────────────
    atr_series = ind.atr(df, atr_len)
    raw_atr = atr_series.tolist()
    atr_baseline = atr_series.rolling(baseline_len).mean().tolist()

    # ── Per-bar factors ─────────────────────────────────────────────
    struct_hi = df["h"].rolling(struct_len).max().tolist()
    struct_lo = df["l"].rolling(struct_len).min().tolist()
    up_moves = (df["c"] > df["c"].shift(1)).rolling(mom_len).sum().tolist()
    down_moves = (df["c"] < df["c"].shift(1)).rolling(mom_len).sum().tolist()

    has_volume = "v" in df.columns and df["v"].notna().any() and (df["v"].fillna(0) > 0).any()
    vol_z = None
    if cfg.SATS_VOL_MODE == "VOLUME" and has_volume:
        v = df["v"].astype(float)
        vmean = v.rolling(cfg.SATS_VOL_Z_LEN).mean()
        vstd = v.rolling(cfg.SATS_VOL_Z_LEN).std()
        vol_z = ((v - vmean) / vstd).tolist()

    tqi = [0.5] * n
    er_arr = [0.0] * n
    eff_atr = [0.0] * n
    w_er = cfg.SATS_TQI_W_ER
    w_vol = cfg.SATS_TQI_W_VOL
    w_struct = cfg.SATS_TQI_W_STRUCT
    w_mom = cfg.SATS_TQI_W_MOM
    vol_available = cfg.SATS_VOL_MODE == "ATR" or (vol_z is not None)

    for i in range(n):
        er = _efficiency_ratio(close, i, er_len)
        er_arr[i] = er
        ra = raw_atr[i] if not pd.isna(raw_atr[i]) else 0.0
        eff_atr[i] = ra * (0.5 + 0.5 * er) if cfg.SATS_USE_EFF_ATR else ra

        if not cfg.SATS_USE_TQI:
            tqi[i] = 0.5
            continue

        t_er = _clamp(er, 0.0, 1.0)

        # Volatility-regime factor
        if cfg.SATS_VOL_MODE == "ATR":
            base = atr_baseline[i]
            vol_ratio = _safe_div(ra, base, 1.0) if base and not pd.isna(base) else 1.0
            t_vol = _map_clamp(vol_ratio, 0.6, 1.8, 0.0, 1.0)
        elif vol_z is not None and not pd.isna(vol_z[i]):
            t_vol = _map_clamp(vol_z[i], -1.0, 2.0, 0.0, 1.0)
        else:
            t_vol = 0.0

        # Structure
        hi, lo = struct_hi[i], struct_lo[i]
        if pd.isna(hi) or pd.isna(lo):
            t_struct = 0.0
        else:
            price_pos = _safe_div(close[i] - lo, hi - lo, 0.5)
            t_struct = _clamp(abs(price_pos - 0.5) * 2.0, 0.0, 1.0)

        # Momentum persistence
        if i >= mom_len:
            window_change = close[i] - close[i - mom_len]
            um = up_moves[i] if not pd.isna(up_moves[i]) else 0.0
            dm = down_moves[i] if not pd.isna(down_moves[i]) else 0.0
            if window_change > 0:
                t_mom = um / mom_len
            elif window_change < 0:
                t_mom = dm / mom_len
            else:
                t_mom = 0.0
        else:
            t_mom = 0.0

        avail_vol_w = w_vol if vol_available else 0.0
        wsum = w_er + avail_vol_w + w_struct + w_mom
        denom = wsum if wsum > 0 else 1.0
        raw = (t_er * w_er + t_vol * avail_vol_w + t_struct * w_struct + t_mom * w_mom) / denom
        tqi[i] = _clamp(raw, 0.0, 1.0)

    # ── Adaptive multipliers (vectorised, then EMA-smoothed) ────────
    q_strength = cfg.SATS_QUALITY_STRENGTH
    curve = cfg.SATS_QUALITY_CURVE
    active_raw = [0.0] * n
    passive_raw = [0.0] * n
    for i in range(n):
        legacy = (1.0 + cfg.SATS_ADAPT_STRENGTH * (0.5 - er_arr[i])) if cfg.SATS_USE_ADAPTIVE else 1.0
        q_dev = (1.0 - tqi[i]) ** curve if cfg.SATS_USE_TQI else 0.5
        tqi_mult = 1.0 - q_strength + q_strength * (_TQI_MULT_FLOOR + _TQI_MULT_RANGE * q_dev)
        sym = cfg.SATS_BASE_MULT * legacy * tqi_mult
        if cfg.SATS_USE_TQI and cfg.SATS_USE_ASYM:
            tighten = 1.0 - cfg.SATS_ASYM_STRENGTH * tqi[i] * _ASYM_TIGHTEN_MAX
            widen = 1.0 + cfg.SATS_ASYM_STRENGTH * tqi[i] * _ASYM_WIDEN_MAX
            active_raw[i] = sym * tighten
            passive_raw[i] = sym * widen
        else:
            active_raw[i] = sym
            passive_raw[i] = sym

    active_mult = [0.0] * n
    passive_mult = [0.0] * n
    for i in range(n):
        if i == 0:
            active_mult[i] = active_raw[i]
            passive_mult[i] = passive_raw[i]
        elif cfg.SATS_MULT_SMOOTH:
            a = _MULT_SMOOTH_ALPHA
            active_mult[i] = active_mult[i - 1] * (1 - a) + active_raw[i] * a
            passive_mult[i] = passive_mult[i - 1] * (1 - a) + passive_raw[i] * a
        else:
            active_mult[i] = active_raw[i]
            passive_mult[i] = passive_raw[i]

    # ── SuperTrend ratchet + character-flip (path-dependent loop) ───
    lower = [float("nan")] * n
    upper = [float("nan")] * n
    st_trend = [1] * n
    trend_start = 0
    char_win = max(cfg.SATS_CHARFLIP_MIN_AGE, 3)
    flip_up_at = [False] * n
    flip_down_at = [False] * n
    char_flip_at = [False] * n

    for i in range(n):
        a = eff_atr[i]
        if i == 0:
            prev_trend = 1
            lower[i] = src[i] - active_mult[i] * a
            upper[i] = src[i] + passive_mult[i] * a
            st_trend[i] = 1
            trend_start = 0
            continue

        prev_trend = st_trend[i - 1]
        lower_mult = active_mult[i] if prev_trend == 1 else passive_mult[i]
        upper_mult = passive_mult[i] if prev_trend == 1 else active_mult[i]
        lower_raw = src[i] - lower_mult * a
        upper_raw = src[i] + upper_mult * a

        lower[i] = max(lower_raw, lower[i - 1]) if close[i - 1] > lower[i - 1] else lower_raw
        upper[i] = min(upper_raw, upper[i - 1]) if close[i - 1] < upper[i - 1] else upper_raw

        price_flip_up = prev_trend == -1 and close[i] > upper[i - 1]
        price_flip_down = prev_trend == 1 and close[i] < lower[i - 1]

        trend_age = i - trend_start
        char_flip_up = char_flip_down = False
        if cfg.SATS_USE_CHARFLIP and cfg.SATS_USE_TQI and trend_age >= cfg.SATS_CHARFLIP_MIN_AGE and i >= char_win:
            tqi_window_high = max(tqi[i - char_win + 1: i + 1])
            base = tqi_window_high > cfg.SATS_CHARFLIP_HIGH and tqi[i] < cfg.SATS_CHARFLIP_LOW
            if base and prev_trend == 1 and close[i] < close[i - char_win]:
                char_flip_down = True
            elif base and prev_trend == -1 and close[i] > close[i - char_win]:
                char_flip_up = True

        final_up = price_flip_up or char_flip_up
        final_down = price_flip_down or char_flip_down
        st_trend[i] = 1 if final_up else (-1 if final_down else prev_trend)
        if st_trend[i] != prev_trend:
            trend_start = i
            flip_up_at[i] = st_trend[i] == 1
            flip_down_at[i] = st_trend[i] == -1
            char_flip_at[i] = (char_flip_up and not price_flip_up) or (char_flip_down and not price_flip_down)

    # ── Fire only on a flip at the last (just-closed) bar ───────────
    last = n - 1
    if not (flip_up_at[last] or flip_down_at[last]):
        trend_lbl = "up" if st_trend[last] == 1 else "down"
        return None, f"no flip (trend {trend_lbl}, tqi {tqi[last]:.2f})"

    direction = "BUY" if flip_up_at[last] else "SELL"
    entry = close[last]
    a = eff_atr[last]
    if a <= 0:
        return None, "atr non-positive"

    plan = _build_plan(df, last, direction, entry, a)
    if plan is None:
        return None, "invalid trade plan (risk/targets)"

    via_price = (direction == "BUY" and st_trend[last] == 1 and not char_flip_at[last]) or \
                (direction == "SELL" and st_trend[last] == -1 and not char_flip_at[last])
    reason = "Quality collapse" if char_flip_at[last] else "Price band break"

    return {
        "pattern": PATTERN_NAME,
        "direction": direction,
        "entry_price": float(entry),
        "stop_loss": float(plan["stop"]),
        "tp1": float(plan["tp1"]),
        "tp2": float(plan["tp2"]),
        "tp3": float(plan["tp3"]),
        "risk": float(plan["risk"]),
        "setup_quality": float(tqi[last]),
        "tqi": float(tqi[last]),
        "er": float(er_arr[last]),
        "atr": float(a),
        "char_flip": bool(char_flip_at[last]),
        "reason": reason,
        "st_line": float(lower[last] if st_trend[last] == 1 else upper[last]),
        # Keys the shared pipeline expects; SATS is single-timeframe and
        # has no ZLSMA axis, so this stays None (contributes 0 downstream).
        "zlsma_status": None,
        "chop_regime": bool(tqi[last] < cfg.SATS_CHARFLIP_LOW),
        "target_mode": "SATS_DYNAMIC" if cfg.SATS_TP_MODE == "DYNAMIC" else "SATS_FIXED",
    }, None


# ─────────────────────────────────────────────────────────────────────
# Trade plan: pivot-anchored SL + R-multiple TPs (optional dynamic scale)
# ─────────────────────────────────────────────────────────────────────
def _last_pivot(values, length, i, is_high):
    """Most recent confirmed pivot at or before bar i, and its bar index.

    A pivot at bar p needs `length` bars on each side, so it is only
    confirmed at bar p+length. Returns (price, bar) or (None, None)."""
    best_price = None
    best_bar = None
    for p in range(length, i - length + 1):
        seg = values[p - length: p + length + 1]
        centre = values[p]
        if is_high and centre == max(seg) and seg.count(centre) == 1:
            best_price, best_bar = centre, p
        elif not is_high and centre == min(seg) and seg.count(centre) == 1:
            best_price, best_bar = centre, p
    return best_price, best_bar


def _build_plan(df, i, direction, entry, atr_value):
    high = df["h"].astype(float).tolist()
    low = df["l"].astype(float).tolist()
    length = cfg.SATS_PIVOT_LEN

    if direction == "BUY":
        piv_price, piv_bar = _last_pivot(low, length, i, is_high=False)
        valid = piv_price is not None and (i - piv_bar) <= cfg.SATS_PIVOT_MAX_AGE and piv_price < entry
        base = piv_price if valid else low[i]
    else:
        piv_price, piv_bar = _last_pivot(high, length, i, is_high=True)
        valid = piv_price is not None and (i - piv_bar) <= cfg.SATS_PIVOT_MAX_AGE and piv_price > entry
        base = piv_price if valid else high[i]

    buffer = cfg.SATS_SL_MULT * atr_value
    cap = max(cfg.SATS_SL_MAX_DIST, cfg.SATS_SL_MULT) * atr_value

    if direction == "BUY":
        stop = max(min(base - buffer, entry - buffer), entry - cap)
        risk = entry - stop
    else:
        stop = min(max(base + buffer, entry + buffer), entry + cap)
        risk = stop - entry

    if risk <= 0:
        return None

    r1, r2, r3 = _tp_r_multiples(df, i, atr_value)
    if direction == "BUY":
        tp1, tp2, tp3 = entry + risk * r1, entry + risk * r2, entry + risk * r3
    else:
        tp1, tp2, tp3 = entry - risk * r1, entry - risk * r2, entry - risk * r3

    return {"stop": stop, "risk": risk, "tp1": tp1, "tp2": tp2, "tp3": tp3}


def _tp_r_multiples(df, i, atr_value):
    """Base R-multiples, sorted; optionally scaled by TQI + vol regime."""
    r1, r2, r3 = sorted((cfg.SATS_TP1_R, cfg.SATS_TP2_R, cfg.SATS_TP3_R))
    if cfg.SATS_TP_MODE != "DYNAMIC":
        return r1, r2, r3

    atr_series = ind.atr(df, cfg.SATS_ATR_LEN)
    baseline = atr_series.rolling(cfg.SATS_ATR_BASELINE_LEN).mean()
    ra = atr_series.iloc[i]
    base = baseline.iloc[i]
    vol_ratio = _safe_div(ra, base, 1.0) if base and not pd.isna(base) else 1.0

    er = _efficiency_ratio(df["c"].astype(float).tolist(), i, cfg.SATS_ER_LEN)
    q_dev = (1.0 - er) ** cfg.SATS_QUALITY_CURVE
    tqi_comp = _clamp(1.0 - q_dev, 0.0, 1.0)  # proxy; full TQI recomputed in evaluate
    vol_comp = _clamp(_map_clamp(vol_ratio, 0.5, 2.0, 0.0, 1.0), 0.0, 1.0)
    wsum = cfg.SATS_DYN_TP_TQI_W + cfg.SATS_DYN_TP_VOL_W
    denom = wsum if wsum > 0 else 1.0
    raw = (tqi_comp * cfg.SATS_DYN_TP_TQI_W + vol_comp * cfg.SATS_DYN_TP_VOL_W) / denom
    scale = cfg.SATS_DYN_TP_MIN + raw * (cfg.SATS_DYN_TP_MAX - cfg.SATS_DYN_TP_MIN) if wsum > 0 else 1.0

    ceil_r = cfg.SATS_DYN_TP_CEIL_R
    f1 = min(cfg.SATS_DYN_TP_FLOOR_R1, ceil_r)
    f2 = min(cfg.SATS_DYN_TP_FLOOR_R1 * (r2 / max(r1, 0.01)), ceil_r)
    f3 = min(cfg.SATS_DYN_TP_FLOOR_R1 * (r3 / max(r1, 0.01)), ceil_r)
    e1 = _clamp(r1 * scale, f1, ceil_r)
    e2 = _clamp(r2 * scale, f2, ceil_r)
    e3 = _clamp(r3 * scale, f3, ceil_r)
    return sorted((e1, e2, e3))
