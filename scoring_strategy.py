"""
Gold-only trading alert bot — scoring engine.

    M15 SETUP (Golden Trio / SMC) creates the opportunity.
    M5 / H1 / H4 / M1 modify confidence. They do not veto.

Score is assembled from three groups on one 0..100 scale:

  A. SETUP    setup_quality * SCORE_SETUP_MAX, plus a signed
              trend-alignment axis (ZLSMA slope for GT, the same slope
              measured against the structural direction for SMC).
  B. MTF      strategy.mtf -- H4 regime, H1 context, M5 confirmation,
              M1 entry timing. Each signed and bounded; unavailable ==
              neutral == exactly 0 points.
  C. CONTEXT  round-number confluence, chop-regime penalty, ATR
              sweet-spot penalty.

Tiers:
    WATCH: score >= WATCH_MIN_SCORE
    A+   : score >= APLUS_MIN_SCORE

There are deliberately **no post-score vetoes**. An unfavourable H4
regime, a flat or opposing ZLSMA, and a chop regime all cost points;
none of them can take a 75-point setup and refuse to call it A+. The
penalties are sized so a setup with genuinely bad context cannot reach
the A+ threshold on setup quality alone.

Two things this file does NOT claim:
  - that a GT setup_quality of 0.8 and an SMC setup_quality of 0.8 carry
    the same expectancy. They share an axis and a budget; whether they
    share a *meaning* is measured by tools/calibrate_scores.py.
  - that any particular score maps to any particular win rate. The
    thresholds are structural defaults, not calibrated ones, until a
    large out-of-sample run says otherwise.

PERF: accepts pre-built DataFrames to avoid per-call reconstruction.
"""
import json
import os

import pandas as pd

import market_sessions
import scoring_indicators as ind
import strategy_config as cfg
from strategy import mtf, targets
from strategy.golden_trio import (
    find_golden_trio_candidate,
    find_golden_trio_candidate_diag,
    market_context,
)
from strategy.smc_detector import find_smc_candidate


# ────────────────────────────────────────────────────────────────██[...]
# Higher-timeframe bias
# ────────────────────────────────────────────────────────────────██[...]
def _ensure_df(data):
    """Convert candle list to DataFrame if needed; pass-through if already a DF."""
    if isinstance(data, pd.DataFrame):
        return data
    return pd.DataFrame(data)


def htf_bias(candles_h4, flat_band_pct=cfg.MTF_H4_FLAT_BAND_PCT):
    """Bull / bear / flat from H4 candles.

    Thin wrapper over strategy.mtf.h4_regime so the regime label shown in
    alerts and the H4 points added to the score can never disagree --
    there is one implementation, not two. The direction passed is
    irrelevant to the returned label.
    """
    return mtf.h4_regime(candles_h4 or [], "BUY", flat_band_pct=flat_band_pct)["bias"]


def opposes(bias, direction):
    """True when an H4 regime label contradicts a trade direction.

    No longer used for scoring -- H4 is a continuous signed contribution
    in strategy.mtf now, not a boolean. Retained because the alert
    formatting and diagnostics still want the plain-English label.
    """
    return (bias == "BULL" and direction == "SELL") or (bias == "BEAR" and direction == "BUY")


def aligns(bias, direction):
    """True when an H4 regime label agrees with a trade direction."""
    return (bias == "BULL" and direction == "BUY") or (bias == "BEAR" and direction == "SELL")


# Back-compat aliases.
_opposes = opposes
_aligns = aligns


# ────────────────────────────────────────────────────────────────██[...]
# Candidate discovery + scoring
# ────────────────────────────────────────────────────────────────██[...]
SMC_PATTERNS = ("ORDER_BLOCK", "CHOCH_REVERSAL", "SMC_LIQUIDITY_SWEEP")


def _setup_quality(cand):
    """Both detectors' quality on ONE 0..1 axis.

    Golden Trio reports setup_quality directly (weighted RSI + Turtle
    evidence). SMC reports a 0..PATTERN_QUALITY_BASE_MAX integer, divided
    here. Older candidate dicts without setup_quality fall back to the
    legacy sum so nothing crashes mid-upgrade.

    Sharing an axis makes the two *comparable*; it does not make them
    *equivalent*. Run tools/calibrate_scores.py to find out whether GT
    0.8 and SMC 0.8 actually earn the same expectancy, and reweight
    GT_QUALITY_WEIGHT_* / PATTERN_QUALITY_BASE_MAX from that, not from
    the fact that both now happen to end at 1.0.
    """
    if not cand:
        return -1.0
    q = cand.get("setup_quality")
    if q is not None:
        return float(max(0.0, min(1.0, q)))
    if cand.get("quality") is not None:
        qmax = cand.get("quality_max") or cfg.PATTERN_QUALITY_BASE_MAX
        return float(max(0.0, min(1.0, cand["quality"] / qmax))) if qmax else 0.0
    legacy = cand.get("rsi_quality", 0) + cand.get("turtle_quality", 0)
    return float(max(0.0, min(1.0, legacy / 50.0)))


def _attach_targets_if_missing(cand, entry_candles=None, target_mode=None):
    """SMC detectors return sweep_price + direction; attach the same
    entry/stop/TP ladder Golden Trio uses so score_candidate sees a
    uniform candidate shape. Uses strategy.targets so FIXED and ATR modes
    behave identically for both detectors."""
    if not cand or "entry_price" in cand:
        return cand
    mode = target_mode or cfg.TARGET_MODE
    if mode not in ("FIXED", "ATR"):
        return cand   # structural SMC entry/exit not implemented here
    entry = cand.get("sweep_price")
    if entry is None:
        return cand
    atr_value = cand.get("atr") or targets.atr_from_candles(entry_candles)
    t = targets.build_targets(entry, cand["direction"], atr_value=atr_value, mode=mode)
    if not t:
        return cand
    cand["entry_price"] = float(entry)
    cand["stop_loss"] = float(t["stop_loss"])
    cand["tp1"] = float(t["tp1"])
    cand["tp2"] = float(t["tp2"])
    cand["tp3"] = float(t["tp3"])
    cand["risk"] = float(t["risk"])
    cand["target_mode"] = t["target_mode"]
    return cand


def _attach_context(cand, entry_candles):
    """Give an SMC candidate the same trend-alignment and chop axes
    Golden Trio produces, measured the same way on the same candles.

    Without this, SMC candidates carried no zlsma_status at all and the
    trend axis was simply skipped for half the signals -- which is not a
    fair comparison between detectors, it's a different score formula
    per detector."""
    if not cand or cand.get("zlsma_status") is not None:
        return cand
    ctx = market_context(entry_candles, cand["direction"])
    if ctx:
        cand["zlsma_status"] = ctx["zlsma_status"]
        cand.setdefault("chop_regime", ctx["chop_regime"])
        cand.setdefault("atr", ctx["atr"])
    return cand


def _prepare_smc(cand, entry_candles, target_mode=None):
    cand = _attach_context(cand, entry_candles)
    return _attach_targets_if_missing(cand, entry_candles, target_mode=target_mode)


# Back-compat alias for callers/tests that imported the old name.
def _add_fixed_targets_if_missing(cand):
    return _attach_targets_if_missing(cand)


def _normalized_quality(cand):
    """Deprecated alias for _setup_quality; kept for external callers."""
    return _setup_quality(cand)


def find_candidate(entry_candles, target_mode=None):
    """Run both detectors; return the candidate with the higher setup
    quality. Ties broken by GT preference (mean-reversion is the primary)."""
    gt = find_golden_trio_candidate(entry_candles, target_mode=target_mode)
    smc = _prepare_smc(find_smc_candidate(entry_candles), entry_candles, target_mode)
    if not gt and not smc:
        return None
    if not smc:
        return gt
    if not gt:
        return smc
    return smc if _setup_quality(smc) > _setup_quality(gt) else gt


def find_candidate_diag(entry_candles, target_mode=None):
    """(candidate_or_None, block_reason_str). Runs both detectors; reports
    which one fired, or the GT block reason if neither did."""
    smc = _prepare_smc(find_smc_candidate(entry_candles), entry_candles, target_mode)
    gt, gt_reason = find_golden_trio_candidate_diag(entry_candles, target_mode=target_mode)
    if smc and gt:
        winner = smc if _setup_quality(smc) > _setup_quality(gt) else gt
        return winner, None
    if smc:
        return smc, None
    if gt:
        return gt, None
    return None, f"GT: {gt_reason} | SMC: none"


def score_candidate(instrument, instrument_class, candidate, market, now_utc, level_store,
                    pending_store=None, mode=None, entry_df=None):
    """Score a candidate from scratch (base 0).

    Args:
        market: dict with "entry" plus any of "m1"/"m5"/"h1"/"h4". Missing
                timeframes contribute exactly 0 -- see strategy.mtf.
        entry_df: Pre-built DataFrame for market["entry"]. If None, built
                  on-demand. PERF: pass this to avoid reconstruction.
    """
    if candidate is None:
        return None

    direction = candidate["direction"]
    zlsma_status = candidate.get("zlsma_status")
    is_smc = candidate["pattern"] in SMC_PATTERNS

    score = 0
    breakdown = []

    # ── A. M15 setup ────────────────────────────────────────────────
    quality = _setup_quality(candidate)
    setup_pts = int(round(quality * cfg.SCORE_SETUP_MAX))
    score += setup_pts
    setup_tag = f"smc_{candidate['pattern'].lower()}" if is_smc else "gt_setup"
    breakdown.append((setup_tag, setup_pts))

    # Trend-alignment axis. Same points, same measurement, both detectors.
    if zlsma_status == "aligned":
        score += cfg.SCORE_ZLSMA_ALIGNED
        breakdown.append(("zlsma_aligned", cfg.SCORE_ZLSMA_ALIGNED))
    elif zlsma_status == "against":
        score += cfg.SCORE_ZLSMA_AGAINST
        breakdown.append(("zlsma_against", cfg.SCORE_ZLSMA_AGAINST))
    else:  # "flat" or unavailable
        breakdown.append(("zlsma_flat", 0))

    # ── B. MTF layer ────────────────────────────────────────────────
    mtf_pts, mtf_rows, mtf_detail = mtf.evaluate(
        market, direction, entry_price=candidate.get("entry_price"))
    score += mtf_pts
    breakdown.extend(mtf_rows)
    bias = mtf_detail["h4"].get("bias", "FLAT")

    # ── C. Context ──────────────────────────────────────────────────
    if candidate.get("chop_regime"):
        score += cfg.SCORE_CHOP_PENALTY
        breakdown.append(("chop_regime", cfg.SCORE_CHOP_PENALTY))

    killzone_pts, killzone_tag = market_sessions.killzone_score(now_utc, instrument_class)
    killzone_pts = min(killzone_pts, cfg.SCORE_KILLZONE_MAX)
    if killzone_pts:
        score += killzone_pts
        breakdown.append((killzone_tag, killzone_pts))

    rn_pts = ind.round_number_bonus(candidate["entry_price"], instrument)
    if rn_pts:
        rn_pts = min(rn_pts, cfg.SCORE_ROUND_NUMBER)
        score += rn_pts
        breakdown.append(("round_number", rn_pts))

    # PERF: use pre-built entry_df if provided, avoid reconstruction.
    if entry_df is None:
        entry_df = _ensure_df(market["entry"])
    atr_pts, atr_tag = ind.atr_sweet_spot_penalty(entry_df, mode=mode)
    if atr_pts:
        atr_pts = max(atr_pts, cfg.SCORE_ATR_SWEET_SPOT_PENALTY)
        score += atr_pts
        breakdown.append((atr_tag, atr_pts))

    score = max(0, min(100, score))

    # ── Tier ────────────────────────────────────────────────────────
    # No post-score vetoes. Everything that used to block A+ here (H4
    # opposed, chop regime, non-aligned ZLSMA) is now a signed score
    # contribution above, so "this is a 75-point setup that we refuse to
    # call A+" can no longer happen. A setup carrying all three
    # negatives loses roughly 34 points, which is more than enough to
    # keep it under APLUS_MIN_SCORE without a separate gate.
    m = mode
    aplus_min = m.aplus_min_score if m is not None else cfg.APLUS_MIN_SCORE
    watch_min = m.watch_min_score if m is not None else cfg.WATCH_MIN_SCORE
    aplus_eligible = score >= aplus_min
    tier = "A+" if aplus_eligible else ("WATCH" if score >= watch_min else "NONE")

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
        "setup_quality": round(quality, 4),
        "mtf": mtf_detail,
        "mtf_points": mtf_pts,
        "mtf_available": mtf.availability(mtf_detail),
        "target_mode": candidate.get("target_mode", cfg.TARGET_MODE),
        "chop_regime": bool(candidate.get("chop_regime")),
        "rsi": candidate.get("rsi"),
        "zlsma": candidate.get("zlsma"),
        "turtle_upper": candidate.get("turtle_upper"),
        "turtle_lower": candidate.get("turtle_lower"),
    }


# ────────────────────────────────────────────────────────────────██[...]
# Pending A+ store — 1-candle confirmation delay before an alert fires
# ────────────────────────────────────────────────────────────────██[...]
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
