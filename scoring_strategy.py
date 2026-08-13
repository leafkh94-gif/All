"""
Gold-only trading alert bot — scoring engine.
Thin wrapper around the Golden Trio detector plus a small scorer that keeps
the WATCH / A+ tiers the tracker infrastructure expects.
"""
import json
import os

import pandas as pd

import market_sessions
import scoring_indicators as ind
import strategy_config as cfg
from strategy.golden_trio import find_golden_trio_candidate


# ─────────────────────────────────────────────────────────────────────
# Higher-timeframe bias (used as a hard block: no counter-trend entries)
# ─────────────────────────────────────────────────────────────────────
def _df(candles):
    return pd.DataFrame(candles)


def htf_bias(candles_h4, flat_band_pct=0.001):
    """Bull / bear / flat from H4 candles by comparing EMA(20) slope over 5 bars."""
    if not candles_h4 or len(candles_h4) < 30:
        return "FLAT"
    df = _df(candles_h4)
    e = ind.ema(df["c"], 20)
    if len(e) < 6:
        return "FLAT"
    change = (e.iloc[-1] - e.iloc[-6]) / e.iloc[-6]
    if change > flat_band_pct:
        return "BULL"
    if change < -flat_band_pct:
        return "BEAR"
    return "FLAT"


def _opposes(bias, direction):
    return (bias == "BULL" and direction == "SELL") or (bias == "BEAR" and direction == "BUY")


# ─────────────────────────────────────────────────────────────────────
# Candidate discovery + scoring
# ─────────────────────────────────────────────────────────────────────
def find_candidate(entry_candles):
    return find_golden_trio_candidate(entry_candles)


def score_candidate(instrument, instrument_class, candidate, market, now_utc, level_store,
                    pending_store=None, mode=None):
    """Turn a Golden Trio candidate into a scored alert dict.

    Scoring composition (targets A+ >= 75):
      * Base 60 when the detector fires (all four gates already passed)
      * Normalized quality up to +GT_QUALITY_MAX (RSI extremity + wick proximity)
      * Killzone bonus / dead-zone penalty from market_sessions
      * Round-number confluence on entry price
      * ATR sweet-spot penalty for dead/too-volatile regimes
      * H4 opposing bias is a SOFT PENALTY -- the counter-trend setup can
        still fire if the rest of the scoring is strong enough to clear the
        WATCH / A+ thresholds after the deduction.
    """
    if candidate is None:
        return None

    direction = candidate["direction"]

    h4 = market.get("h4") or []
    bias = htf_bias(h4)

    entry_df = _df(market["entry"])
    breakdown = []
    score = 60
    breakdown.append(("base", 60))

    if _opposes(bias, direction):
        score += cfg.HTF_OPPOSED_PENALTY
        breakdown.append(("htf_opposed", cfg.HTF_OPPOSED_PENALTY))

    quality_pts = candidate["quality"]  # already scaled 0..GT_QUALITY_MAX
    score += quality_pts
    breakdown.append(("quality", quality_pts))

    killzone_pts, killzone_tag = market_sessions.killzone_score(now_utc, instrument_class)
    score += killzone_pts
    if killzone_pts:
        breakdown.append((killzone_tag, killzone_pts))

    rn_pts = ind.round_number_bonus(candidate["entry_price"], instrument)
    if rn_pts:
        score += rn_pts
        breakdown.append(("round_number", rn_pts))

    atr_pts, atr_tag = ind.atr_sweet_spot_penalty(entry_df, mode=mode)
    if atr_pts:
        score += atr_pts
        breakdown.append((atr_tag, atr_pts))

    score = max(0, min(100, score))

    return {
        "instrument": instrument,
        "instrument_class": instrument_class,
        "direction": direction,
        "pattern": candidate["pattern"],
        "entry_price": candidate["entry_price"],
        "stop_loss": candidate["stop_loss"],
        "tp1": candidate["tp1"],
        "tp2": candidate["tp2"],
        "tp3": candidate["tp3"],
        "risk": candidate["risk"],
        "score": score,
        "breakdown": breakdown,
        "htf_bias": bias,
        "rsi": candidate.get("rsi"),
        "zlsma": candidate.get("zlsma"),
        "turtle_upper": candidate.get("turtle_upper"),
        "turtle_lower": candidate.get("turtle_lower"),
    }


# ─────────────────────────────────────────────────────────────────────
# Pending A+ store — 1-candle confirmation delay before an alert fires
# ─────────────────────────────────────────────────────────────────────
class PendingAPlusStore:
    def __init__(self, path=None):
        self.path = path or os.path.join("state", "pending_aplus.json")
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
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

    def add(self, instrument, payload):
        self._data[instrument] = payload
        self._save()

    def get(self, instrument):
        return self._data.get(instrument)

    def remove(self, instrument):
        if instrument in self._data:
            del self._data[instrument]
            self._save()

    def items(self):
        return list(self._data.items())


def confirmation_closed_in_direction(last_closed_candle, direction):
    if not last_closed_candle:
        return False
    o = last_closed_candle["o"]
    c = last_closed_candle["c"]
    return (direction == "BUY" and c > o) or (direction == "SELL" and c < o)
