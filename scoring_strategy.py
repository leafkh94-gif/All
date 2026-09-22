"""
Gold-only trading alert bot — scoring engine (SATS).

The bot runs SATS (Self-Aware Trend System) as its sole strategy. A
confirmed SuperTrend flip on the entry timeframe (M15) is the signal; the
signal is graded by its Trend Quality Index (TQI). See strategy/sats.py for
the engine.

    score = round(100 * TQI)

so the whole downstream — tiers, tools/calibrate_scores.py,
tools/tune_thresholds.py, the walk-forward report — keeps working on the
familiar 0..100 scale. Tiers (strategy_config):

    WATCH: score >= WATCH_MIN_SCORE   (TQI >= 0.35)
    A+   : score >= APLUS_MIN_SCORE   (TQI >= 0.70)

SATS is single-timeframe and self-contained: it does not consult H1/H4/M5/M1
and there is no ZLSMA/SMC/round-number/killzone layer. Those modules remain
in the tree for reference but are no longer on the live path. `market` is
still accepted by score_candidate for interface compatibility (and so the
backtester's look-ahead audit still records the higher-timeframe bar
timestamps), but nothing in it changes a SATS score.

Golden Trio and SMC previously created candidates here; that history and its
evaluation live in git and the README. The one thing worth repeating: a
grade is a description of the current tape, not a probability of profit.
"""
import json
import os

import pandas as pd

import strategy_config as cfg
from strategy import sats


def _ensure_df(data):
    """Convert candle list to DataFrame if needed; pass-through if already a DF."""
    if isinstance(data, pd.DataFrame):
        return data
    return pd.DataFrame(data)


# ────────────────────────────────────────────────────────────────
# Candidate discovery
# ────────────────────────────────────────────────────────────────
def find_candidate(entry_candles, target_mode=None, entry_mode=None):
    """Return a SATS signal for a confirmed flip on the last candle, or None.

    target_mode / entry_mode are accepted for backwards compatibility with
    the backtester's call sites but do not apply to SATS: it is a single
    trend-following model with its own TP mode (cfg.SATS_TP_MODE) and no
    reversion/momentum variants. They are ignored.
    """
    return sats.evaluate(entry_candles)


def find_candidate_diag(entry_candles, target_mode=None, entry_mode=None):
    """(candidate_or_None, block_reason_str) — see strategy.sats.evaluate_diag."""
    sig, reason = sats.evaluate_diag(entry_candles)
    return sig, (None if sig else f"SATS: {reason}")


# ────────────────────────────────────────────────────────────────
# Scoring
# ────────────────────────────────────────────────────────────────
def _setup_quality(cand):
    """SATS setup quality is the TQI directly (0..1)."""
    if not cand:
        return -1.0
    q = cand.get("setup_quality")
    if q is None:
        q = cand.get("tqi", 0.0)
    return float(max(0.0, min(1.0, q)))


def score_candidate(instrument, instrument_class, candidate, market, now_utc, level_store,
                    pending_store=None, mode=None, entry_df=None):
    """Grade a SATS candidate. score = round(100 * TQI); tier from thresholds.

    `market`, `now_utc`, `level_store`, `entry_df` are accepted for interface
    compatibility. SATS grades purely on TQI, so they do not affect the
    score — but keeping the signature lets the live loop and the backtester
    call this unchanged.
    """
    if candidate is None:
        return None

    tqi = _setup_quality(candidate)
    score = int(round(100 * tqi))

    m = mode
    aplus_min = m.aplus_min_score if m is not None else cfg.APLUS_MIN_SCORE
    watch_min = m.watch_min_score if m is not None else cfg.WATCH_MIN_SCORE
    aplus_eligible = score >= aplus_min
    tier = "A+" if aplus_eligible else ("WATCH" if score >= watch_min else "NONE")

    # Breakdown values must be ints: the alert formatter renders them with
    # ":+d". TQI and ER are shown on a 0..100 scale; the flip reason and
    # char-flip flag ride on dedicated keys below, not in the breakdown.
    er = candidate.get("er")
    breakdown = [
        ("sats_tqi", score),
        ("sats_er", int(round(er * 100)) if er is not None else 0),
    ]
    if candidate.get("char_flip"):
        breakdown.append(("char_flip", 1))

    trend_lbl = "UP" if candidate["direction"] == "BUY" else "DOWN"

    return {
        "instrument": instrument,
        "instrument_class": instrument_class,
        "direction": candidate["direction"],
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
        # SATS is single-timeframe: no H4 bias, no ZLSMA, no MTF layer.
        "htf_bias": f"TREND {trend_lbl}",
        "zlsma_status": None,
        "aplus_eligible": aplus_eligible,
        "setup_quality": round(tqi, 4),
        "tqi": round(tqi, 4),
        "char_flip": bool(candidate.get("char_flip")),
        "reason": candidate.get("reason"),
        "mtf": {},
        "mtf_points": 0,
        "mtf_available": "none",
        "target_mode": candidate.get("target_mode", "SATS_FIXED"),
        "chop_regime": bool(candidate.get("chop_regime")),
        "rsi": None,
        "zlsma": None,
        "turtle_upper": None,
        "turtle_lower": None,
        "st_line": candidate.get("st_line"),
    }


# ────────────────────────────────────────────────────────────────
# Pending A+ store — 1-candle confirmation delay before an alert fires
# ────────────────────────────────────────────────────────────────
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
