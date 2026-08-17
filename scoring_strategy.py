"""
Gold-only trading alert bot — scoring engine (component-based, base-0).

Every score component earns its own points; the total is a real measure of
signal quality, not "cleared the artificial 60-point floor". Thresholds:

    WATCH: score >= WATCH_MIN_SCORE (55)
    A+   : score >= APLUS_MIN_SCORE (75) AND all quality gates satisfied

A+ additionally requires:
    - ZLSMA aligned (not flat, not against)
    - H4 not opposed

H4 opposed still WATCH-eligible with a -15 penalty; A+ blocked.
Chop, ZLSMA against, or missing RSI sequence -> no signal at all (from
golden_trio.find_golden_trio_candidate).
"""
import json
import os

import pandas as pd

import market_sessions
import scoring_indicators as ind
import strategy_config as cfg
from strategy.golden_trio import find_golden_trio_candidate, find_golden_trio_candidate_diag


# ─────────────────────────────────────────────────────────────────────
# Higher-timeframe bias
# ─────────────────────────────────────────────────────────────────────
def _df(candles):
    return pd.DataFrame(candles)


def htf_bias(candles_h4, flat_band_pct=0.001):
    """Bull / bear / flat from H4 candles via EMA(20) slope over 5 bars."""
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


def _aligns(bias, direction):
    return (bias == "BULL" and direction == "BUY") or (bias == "BEAR" and direction == "SELL")


# ─────────────────────────────────────────────────────────────────────
# Candidate discovery + scoring
# ─────────────────────────────────────────────────────────────────────
def find_candidate(entry_candles):
    return find_golden_trio_candidate(entry_candles)


def find_candidate_diag(entry_candles):
    """(candidate_or_None, block_reason_str). Preferred over find_candidate
    when the caller wants to log which gate blocked."""
    return find_golden_trio_candidate_diag(entry_candles)


def score_candidate(instrument, instrument_class, candidate, market, now_utc, level_store,
                    pending_store=None, mode=None):
    """Score a Golden Trio candidate from scratch (base 0)."""
    if candidate is None:
        return None

    direction = candidate["direction"]
    bias = htf_bias(market.get("h4") or [])
    zlsma_status = candidate["zlsma_status"]

    score = 0
    breakdown = []

    # 1. Sequenced RSI reversal quality (0..30)
    rsi_pts = candidate.get("rsi_quality", 0)
    score += rsi_pts
    breakdown.append(("rsi_confirm", rsi_pts))

    # 2. Turtle band proximity quality (0..20)
    turtle_pts = candidate.get("turtle_quality", 0)
    score += turtle_pts
    breakdown.append(("turtle_location", turtle_pts))

    # 3. ZLSMA direction (0 or +20; flat contributes 0 and blocks A+)
    if zlsma_status == "aligned":
        score += cfg.SCORE_ZLSMA_ALIGNED
        breakdown.append(("zlsma_aligned", cfg.SCORE_ZLSMA_ALIGNED))
    else:  # "flat" -- "against" already vetoed by golden_trio
        breakdown.append(("zlsma_flat", 0))

    # 4. H4 bias
    if _aligns(bias, direction):
        score += cfg.SCORE_H4_ALIGNED
        breakdown.append(("h4_aligned", cfg.SCORE_H4_ALIGNED))
    elif _opposes(bias, direction):
        score += cfg.SCORE_H4_OPPOSED
        breakdown.append(("h4_opposed", cfg.SCORE_H4_OPPOSED))
    else:
        breakdown.append(("h4_flat", 0))

    # 5. Killzone / session bonus (0..10)
    killzone_pts, killzone_tag = market_sessions.killzone_score(now_utc, instrument_class)
    killzone_pts = min(killzone_pts, cfg.SCORE_KILLZONE_MAX)
    if killzone_pts:
        score += killzone_pts
        breakdown.append((killzone_tag, killzone_pts))

    # 6. Round-number confluence (0 or +5)
    rn_pts = ind.round_number_bonus(candidate["entry_price"], instrument)
    if rn_pts:
        rn_pts = min(rn_pts, cfg.SCORE_ROUND_NUMBER)
        score += rn_pts
        breakdown.append(("round_number", rn_pts))

    # 7. ATR sweet-spot penalty (0 or -10)
    entry_df = _df(market["entry"])
    atr_pts, atr_tag = ind.atr_sweet_spot_penalty(entry_df, mode=mode)
    if atr_pts:
        atr_pts = max(atr_pts, cfg.SCORE_ATR_SWEET_SPOT_PENALTY)
        score += atr_pts
        breakdown.append((atr_tag, atr_pts))

    score = max(0, min(100, score))

    # A+ gate: score threshold AND all quality conditions.
    aplus_eligible = (
        score >= cfg.APLUS_MIN_SCORE
        and zlsma_status == "aligned"
        and not _opposes(bias, direction)
    )
    tier = "A+" if aplus_eligible else ("WATCH" if score >= cfg.WATCH_MIN_SCORE else "NONE")

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
        "tier": tier,
        "breakdown": breakdown,
        "htf_bias": bias,
        "zlsma_status": zlsma_status,
        "aplus_eligible": aplus_eligible,
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
