"""Tests for strategy/smc_detector.py — Order Block, CHOCH, and enhanced
liquidity sweep detection powered by the smartmoneyconcepts library."""
import pytest
from strategy.smc_detector import (
    SMC_AVAILABLE,
    detect_order_block,
    detect_choch_reversal,
    detect_smc_liquidity_sweep,
    find_smc_candidate,
)

pytestmark = pytest.mark.skipif(not SMC_AVAILABLE, reason="smartmoneyconcepts not installed")


def _candle(o, h, l, c, v=100):
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


def _trending_up_candles(n=60, start=100.0, step=0.5):
    """Generate a clear uptrend with swing structure."""
    candles = []
    price = start
    for i in range(n):
        if i % 6 < 4:
            o = price
            c = price + step
            h = c + step * 0.3
            l = o - step * 0.2
            price = c
        else:
            o = price
            c = price - step * 0.4
            h = o + step * 0.2
            l = c - step * 0.2
            price = c
        candles.append(_candle(o, h, l, c))
    return candles


def _reversal_candles(n=60, start=100.0, step=0.5):
    """Generate a trend that reverses mid-way — uptrend then downtrend."""
    candles = []
    price = start
    midpoint = n // 2
    for i in range(n):
        if i < midpoint:
            o = price
            c = price + step
            h = c + step * 0.3
            l = o - step * 0.2
            price = c
        else:
            o = price
            c = price - step
            h = o + step * 0.2
            l = c - step * 0.3
            price = c
        candles.append(_candle(o, h, l, c))
    return candles


def _sweep_candles(n=60, start=100.0):
    """Generate candles with equal lows that get swept."""
    candles = []
    price = start
    for i in range(n):
        if i < 40:
            o = price
            if i in (10, 20, 30):
                l = start - 2.0
                c = price + 0.3
                h = c + 0.2
            else:
                l = price - 0.4
                c = price + 0.3
                h = c + 0.2
            price = c
        elif i == 40:
            o = price
            l = start - 3.5
            c = start - 3.0
            h = o + 0.1
            price = c
        else:
            o = price
            c = price + 0.8
            h = c + 0.3
            l = o - 0.2
            price = c
        candles.append(_candle(o, h, l, c))
    return candles


# ─── Basic contract tests ────────────────────────────────────────────────────

def test_detect_order_block_returns_none_on_too_few_candles():
    assert detect_order_block([_candle(100, 101, 99, 100)] * 10) is None


def test_detect_choch_returns_none_on_too_few_candles():
    assert detect_choch_reversal([_candle(100, 101, 99, 100)] * 10) is None


def test_detect_smc_liquidity_returns_none_on_too_few_candles():
    assert detect_smc_liquidity_sweep([_candle(100, 101, 99, 100)] * 10) is None


def test_find_smc_candidate_returns_none_on_flat_market():
    flat = [_candle(100, 100.1, 99.9, 100)] * 60
    assert find_smc_candidate(flat) is None


# ─── Order Block detection ────────────────────────────────────────────────────

def test_order_block_has_correct_fields():
    candles = _trending_up_candles(80)
    result = detect_order_block(candles)
    if result is not None:
        assert result["pattern"] == "ORDER_BLOCK"
        assert result["direction"] in ("BUY", "SELL")
        assert "ob_top" in result
        assert "ob_bottom" in result
        assert "ob_strength" in result
        assert 0 < result["quality"] <= 38


def test_order_block_direction_makes_sense_in_uptrend():
    candles = _trending_up_candles(80, step=1.0)
    result = detect_order_block(candles)
    if result is not None:
        assert result["direction"] == "BUY"


# ─── CHOCH detection ─────────────────────────────────────────────────────────

def test_choch_has_correct_fields():
    candles = _reversal_candles(80, step=1.0)
    result = detect_choch_reversal(candles)
    if result is not None:
        assert result["pattern"] == "CHOCH_REVERSAL"
        assert result["direction"] in ("BUY", "SELL")
        assert "choch_level" in result
        assert "broken_index" in result
        assert 0 < result["quality"] <= 38


# ─── Enhanced liquidity sweep ─────────────────────────────────────────────────

def test_smc_liquidity_has_correct_fields():
    candles = _sweep_candles(80)
    result = detect_smc_liquidity_sweep(candles)
    if result is not None:
        assert result["pattern"] == "SMC_LIQUIDITY_SWEEP"
        assert result["direction"] in ("BUY", "SELL")
        assert 0 < result["quality"] <= 38


# ─── Integration with find_candidate ──────────────────────────────────────────

def test_find_smc_candidate_picks_highest_quality():
    candles = _trending_up_candles(80, step=1.0)
    result = find_smc_candidate(candles)
    if result is not None:
        assert result["pattern"] in ("ORDER_BLOCK", "CHOCH_REVERSAL", "SMC_LIQUIDITY_SWEEP")
        assert "quality" in result


def test_smc_patterns_appear_in_main_find_candidate():
    """The main find_candidate() in scoring_strategy.py should also consider
    SMC patterns alongside the original 5 detectors."""
    from scoring_strategy import find_candidate
    candles = _trending_up_candles(80, step=1.0)
    result = find_candidate(candles)
    # We can't guarantee a specific pattern fires on synthetic data,
    # but the function should not crash and should accept SMC patterns.
    assert result is None or "pattern" in result


def test_score_candidate_accepts_smc_pattern_types():
    """score_candidate must not crash on the new pattern names."""
    from scoring_strategy import score_candidate, find_candidate
    import scoring_indicators as ind

    candles = _trending_up_candles(80, step=1.0)
    candidate = find_candidate(candles)
    if candidate is None:
        pytest.skip("no pattern detected on synthetic data")

    h1 = _trending_up_candles(160, step=2.0)
    h4 = _trending_up_candles(260, step=4.0)
    market = {"entry": candles, "m15": candles, "h1": h1, "h4": h4}
    level_store = ind.LevelStore()

    import datetime as dt
    now = dt.datetime(2026, 8, 5, 14, 0, tzinfo=dt.timezone.utc)

    result = score_candidate(
        "XAUUSD", "COMMODITY", candidate, market, now, level_store)
    assert result is not None
    assert "score" in result


# ─────────────────────────────────────────────────────────────────────
# Parity with Golden Trio in the scoring pipeline.
#
# SMC candidates used to reach the scorer without a zlsma_status at all,
# so the trend axis was silently skipped for half of all signals. That
# isn't one score formula with two inputs, it's two formulas -- and it
# makes any comparison between the detectors meaningless.
# ─────────────────────────────────────────────────────────────────────
import scoring_strategy as strat
import strategy_config as cfg


def _smc_bearing_candles(n=400, seed=11):
    """A deterministic random walk with enough swing structure that the
    SMC detectors actually fire. A tidy alternating-leg series does not:
    the library's swing detection needs irregular pivots, and a test that
    silently skips because nothing fired proves nothing."""
    import random
    rnd = random.Random(seed)
    out, price = [], 2000.0
    for i in range(n):
        o = price
        c = o + sum(rnd.gauss(0, 0.55) for _ in range(4))
        h = max(o, c) + abs(rnd.gauss(0, 0.55))
        l = min(o, c) - abs(rnd.gauss(0, 0.55))
        out.append({"t": f"2025-01-{1 + i // 96:02d}T{(i % 96) // 4:02d}:{(i % 4) * 15:02d}:00+00:00",
                    "o": round(o, 3), "h": round(h, 3), "l": round(l, 3),
                    "c": round(c, 3), "v": 100})
        price = c
    return out


def _first_smc_candidate(candles):
    for i in range(120, len(candles)):
        raw = find_smc_candidate(candles[:i + 1])
        if raw:
            return dict(raw), candles[:i + 1]
    raise AssertionError(
        "no SMC candidate on the fixture series -- these tests would be "
        "vacuous. Fix the fixture, don't skip the assertion.")


def test_prepared_smc_candidate_has_the_same_shape_as_a_gt_candidate():
    raw, window = _first_smc_candidate(_smc_bearing_candles())
    prepared = strat._prepare_smc(raw, window)
    for key in ("entry_price", "stop_loss", "tp1", "tp2", "tp3", "risk",
                "zlsma_status", "setup_quality"):
        assert key in prepared, f"SMC candidate is missing {key}"


def test_smc_candidates_are_measured_on_the_trend_axis():
    """The specific regression: an SMC candidate must carry a real
    zlsma_status, not None, or the trend axis scores 0 for it by default
    while Golden Trio pays or earns on the same axis."""
    raw, window = _first_smc_candidate(_smc_bearing_candles())
    prepared = strat._prepare_smc(raw, window)
    assert prepared["zlsma_status"] in ("aligned", "flat", "against")


def test_smc_setup_quality_is_on_the_shared_0_1_axis():
    raw, window = _first_smc_candidate(_smc_bearing_candles())
    prepared = strat._prepare_smc(raw, window)
    q = strat._setup_quality(prepared)
    assert 0.0 <= q <= 1.0
    assert abs(q - raw["quality"] / cfg.PATTERN_QUALITY_BASE_MAX) < 1e-9


def test_smc_setup_points_share_the_same_budget_as_golden_trio():
    """Neither detector may outspend the other on setup quality."""
    raw, window = _first_smc_candidate(_smc_bearing_candles())
    prepared = strat._prepare_smc(raw, window)
    import datetime as dt
    market = {"entry": window, "m15": window, "h1": [], "h4": [], "m5": [], "m1": []}
    scored = strat.score_candidate(
        "XAUUSD", "COMMODITY", prepared, market,
        dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc), None)
    assert scored is not None
    setup_pts = next(pts for tag, pts in scored["breakdown"] if tag.startswith("smc_"))
    assert 0 <= setup_pts <= cfg.SCORE_SETUP_MAX
    assert 0 <= scored["score"] <= 100
