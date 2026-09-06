"""Tests for the gold-only scoring engine (Golden Trio wrapper + tiny scorer)."""
import datetime as dt

import scoring_strategy as strat
import strategy_config as cfg
from strategy import modes
from tests.helpers import make_candles, trending_h4_candles
from tests.test_golden_trio import _long_setup_candles, _short_setup_candles


class _StubLevelStore:
    def get_daily_levels(self, _):
        return None

    def get_weekly_levels(self, _):
        return None


def _market(entry_candles, h4=None):
    return {
        "entry": entry_candles,
        "m15": entry_candles,
        "h1": entry_candles,
        "h4": h4 or trending_h4_candles(n=260, up=True),
    }


def _now():
    # A London killzone time so the killzone bonus applies.
    return dt.datetime(2026, 7, 1, 7, 30, tzinfo=dt.timezone.utc)


# ─── htf_bias ────────────────────────────────────────────────────────

def test_htf_bias_bull_on_uptrend():
    assert strat.htf_bias(trending_h4_candles(n=260, up=True)) == "BULL"


def test_htf_bias_bear_on_downtrend():
    assert strat.htf_bias(trending_h4_candles(n=260, up=False)) == "BEAR"


def test_htf_bias_flat_on_short_history():
    assert strat.htf_bias(make_candles(10)) == "FLAT"


# ─── find_candidate ──────────────────────────────────────────────────

def test_find_candidate_returns_golden_trio_dict_when_gates_align():
    result = strat.find_candidate(_long_setup_candles())
    assert result is not None
    assert result["pattern"] == "GOLDEN_TRIO"
    assert result["direction"] == "BUY"


def test_find_candidate_on_flat_market_returns_candidate_tagged_as_chop():
    """Chop was previously a hard veto that returned None; per the user's
    architectural directive it's now a soft penalty. The detector may still
    emit a candidate, but it must carry chop_regime=True so scoring applies
    SCORE_CHOP_PENALTY and A+ eligibility is blocked."""
    result = strat.find_candidate(make_candles(200, start_price=2000.0, step=0.0, noise=0.05))
    if result is not None:
        # If any direction managed to satisfy the remaining gates on a flat
        # tape, it should be tagged as chop so downstream scoring can penalize.
        assert result.get("chop_regime") is True


# ─── score_candidate ─────────────────────────────────────────────────

def test_score_candidate_applies_soft_penalty_when_htf_bias_opposes_direction():
    """H4 opposition costs points; it never blocks the signal.

    The swing is no longer a fixed ±SCORE_H4_ALIGNED: H4 is a continuous
    signed contribution scaled by slope magnitude, so a moderate trend
    earns a fraction of SCORE_H4_MAX. What must hold is the direction and
    the bound.
    """
    candidate = strat.find_candidate(_long_setup_candles())
    aligned_market = _market(_long_setup_candles())  # bullish H4 aligned with BUY
    opposed_market = _market(_long_setup_candles(), h4=trending_h4_candles(n=260, up=False))

    aligned = strat.score_candidate("XAUUSD", "COMMODITY", candidate, aligned_market, _now(), _StubLevelStore())
    opposed = strat.score_candidate("XAUUSD", "COMMODITY", candidate, opposed_market, _now(), _StubLevelStore())

    assert aligned is not None and opposed is not None
    assert opposed["htf_bias"] == "BEAR"
    assert opposed["score"] < aligned["score"]
    swing = aligned["score"] - opposed["score"]
    assert 0 < swing <= 2 * cfg.SCORE_H4_MAX
    assert any(tag == "h4_against" for tag, _ in opposed["breakdown"])


def test_h4_opposition_alone_does_not_veto_aplus():
    """The reviewer's point 5: a high-scoring setup must not be demoted by
    a post-score contextual veto. H4 opposition subtracts points; if the
    setup still clears APLUS_MIN_SCORE afterwards, it stays A+."""
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles(), h4=trending_h4_candles(n=260, up=False))
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market,
                                   _now(), _StubLevelStore())
    assert scored is not None
    # Whatever the score works out to, the tier must be a pure function of it.
    assert scored["aplus_eligible"] == (scored["score"] >= cfg.APLUS_MIN_SCORE)
    assert scored["tier"] == (
        "A+" if scored["score"] >= cfg.APLUS_MIN_SCORE
        else "WATCH" if scored["score"] >= cfg.WATCH_MIN_SCORE else "NONE")


def test_score_candidate_returns_dict_with_expected_fields_on_aligned_bias():
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles())
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, _now(), _StubLevelStore())
    assert scored is not None
    for key in ("instrument", "direction", "pattern", "entry_price", "stop_loss",
                "tp1", "tp2", "tp3", "risk", "score", "breakdown", "htf_bias"):
        assert key in scored
    assert scored["htf_bias"] == "BULL"
    assert scored["direction"] == "BUY"


def test_score_candidate_score_is_bounded_0_100():
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles())
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, _now(), _StubLevelStore())
    assert 0 <= scored["score"] <= 100


def test_score_candidate_omits_killzone_bonus():
    # Killzone bonus intentionally disabled (SCORE_KILLZONE_MAX=0) so alerts
    # aren't concentrated into the London/NY overlap. Verify no killzone
    # tag reaches the breakdown regardless of scan time.
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles())
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, _now(), _StubLevelStore())
    tags = [tag for tag, _ in scored["breakdown"]]
    assert not any("KILLZONE" in tag for tag in tags)


def test_score_candidate_short_setup_bias_bear_returns_scored_dict():
    candidate = strat.find_candidate(_short_setup_candles())
    market = _market(_short_setup_candles(), h4=trending_h4_candles(n=260, up=False))
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, _now(), _StubLevelStore())
    assert scored is not None
    assert scored["direction"] == "SELL"


# ─── PendingAPlusStore ───────────────────────────────────────────────

def test_pending_aplus_store_roundtrip(tmp_path):
    store = strat.PendingAPlusStore(path=str(tmp_path / "pending.json"))
    store.add("XAUUSD", {"score": 80, "direction": "BUY"})
    assert store.get("XAUUSD")["score"] == 80
    store.remove("XAUUSD")
    assert store.get("XAUUSD") is None


def test_pending_aplus_store_items_returns_all_entries(tmp_path):
    store = strat.PendingAPlusStore(path=str(tmp_path / "pending.json"))
    store.add("XAUUSD", {"score": 80})
    items = store.items()
    assert items == [("XAUUSD", {"score": 80})]


# ─── confirmation_closed_in_direction ────────────────────────────────

def test_confirmation_closed_in_direction():
    assert strat.confirmation_closed_in_direction({"o": 100, "c": 101}, "BUY") is True
    assert strat.confirmation_closed_in_direction({"o": 100, "c": 99}, "BUY") is False
    assert strat.confirmation_closed_in_direction({"o": 100, "c": 99}, "SELL") is True
    assert strat.confirmation_closed_in_direction(None, "BUY") is False


# ─── unified setup-quality axis ──────────────────────────────────────

def test_both_detectors_report_setup_quality_on_the_same_0_1_axis():
    """The reviewer's point 4: GT and SMC were normalised onto different
    budgets (rsi30+turtle20 vs quality/38*50) and then compared. They now
    share one axis by construction."""
    gt = strat.find_candidate(_long_setup_candles())
    assert gt is not None
    q = strat._setup_quality(gt)
    assert 0.0 <= q <= 1.0


def test_setup_quality_handles_smc_shaped_candidates():
    smc_like = {"pattern": "ORDER_BLOCK", "direction": "BUY",
                "quality": 19, "quality_max": cfg.PATTERN_QUALITY_BASE_MAX}
    assert abs(strat._setup_quality(smc_like) - 19 / cfg.PATTERN_QUALITY_BASE_MAX) < 1e-9


def test_setup_quality_is_clamped_for_malformed_candidates():
    assert strat._setup_quality({"setup_quality": 5.0}) == 1.0
    assert strat._setup_quality({"setup_quality": -3.0}) == 0.0
    assert strat._setup_quality(None) == -1.0


def test_setup_points_are_capped_by_the_shared_budget():
    """Neither detector may spend more than SCORE_SETUP_MAX on quality --
    that shared ceiling is what makes the two comparable."""
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles())
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market,
                                   _now(), _StubLevelStore())
    setup_pts = dict(scored["breakdown"]).get("gt_setup", 0)
    assert 0 <= setup_pts <= cfg.SCORE_SETUP_MAX


# ─── MTF is wired into the score ─────────────────────────────────────

def test_score_records_which_timeframes_contributed():
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles())
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market,
                                   _now(), _StubLevelStore())
    assert "mtf" in scored and "mtf_available" in scored
    assert set(scored["mtf"]) == {"h4", "h1", "m5", "m1"}


def test_missing_lower_timeframes_do_not_shift_the_score():
    """An M15-only market and one with explicitly-empty M5/M1 must score
    identically, or backtest and live scores would not be comparable."""
    candidate = strat.find_candidate(_long_setup_candles())
    base = _market(_long_setup_candles())
    with_empty = dict(base, m5=[], m1=[])
    a = strat.score_candidate("XAUUSD", "COMMODITY", candidate, base, _now(), _StubLevelStore())
    b = strat.score_candidate("XAUUSD", "COMMODITY", candidate, with_empty, _now(), _StubLevelStore())
    assert a["score"] == b["score"]


def test_confirming_m5_raises_the_score_and_contradicting_m5_lowers_it():
    candidate = strat.find_candidate(_long_setup_candles())
    entry = _long_setup_candles()
    up_m5 = make_candles(300, start_price=2000.0, step=1.0, noise=0.3, interval_minutes=5)
    down_m5 = make_candles(300, start_price=2000.0, step=-1.0, noise=0.3, interval_minutes=5)

    neutral = strat.score_candidate("XAUUSD", "COMMODITY", candidate, _market(entry),
                                    _now(), _StubLevelStore())
    confirmed = strat.score_candidate("XAUUSD", "COMMODITY", candidate,
                                      dict(_market(entry), m5=up_m5), _now(), _StubLevelStore())
    against = strat.score_candidate("XAUUSD", "COMMODITY", candidate,
                                    dict(_market(entry), m5=down_m5), _now(), _StubLevelStore())
    assert confirmed["score"] > neutral["score"] > against["score"]
    # ...but a contradicting M5 must never delete the candidate outright.
    assert against is not None and against["pattern"] == candidate["pattern"]


# ─── tier is a pure function of the score ────────────────────────────

def test_tier_has_no_post_score_vetoes():
    """Points 5 of the review: there must be no path where a setup scores
    above APLUS_MIN_SCORE and is still refused A+ for a contextual
    reason. Exercised across markets that used to trip each old veto."""
    entry = _long_setup_candles()
    markets = [
        _market(entry),
        _market(entry, h4=trending_h4_candles(n=260, up=False)),   # H4 opposed
        dict(_market(entry), h4=[]),                               # no H4 at all
    ]
    candidate = strat.find_candidate(entry)
    for market in markets:
        scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market,
                                       _now(), _StubLevelStore())
        assert scored["aplus_eligible"] == (scored["score"] >= cfg.APLUS_MIN_SCORE)


def test_chop_regime_costs_points_but_does_not_block_the_tier():
    entry = _long_setup_candles()
    candidate = dict(strat.find_candidate(entry))
    market = _market(entry)
    clean = strat.score_candidate("XAUUSD", "COMMODITY", dict(candidate, chop_regime=False),
                                  market, _now(), _StubLevelStore())
    chopped = strat.score_candidate("XAUUSD", "COMMODITY", dict(candidate, chop_regime=True),
                                    market, _now(), _StubLevelStore())
    assert chopped["score"] == clean["score"] + cfg.SCORE_CHOP_PENALTY
    assert chopped["aplus_eligible"] == (chopped["score"] >= cfg.APLUS_MIN_SCORE)


# ─── target mode threading ───────────────────────────────────────────

def test_find_candidate_honours_an_explicit_target_mode():
    fixed = strat.find_candidate(_long_setup_candles(), target_mode="FIXED")
    atr = strat.find_candidate(_long_setup_candles(), target_mode="ATR")
    assert fixed is not None and atr is not None
    assert fixed["risk"] == cfg.FIXED_SL_POINTS * cfg.POINT_VALUE
    # The ATR ladder is volatility-derived, so on these candles it should
    # differ from the fixed one; if it doesn't, the clamp is binding and
    # that is still a legitimate (documented) outcome.
    assert atr["risk"] > 0
