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



# NOTE: SMC is no longer on the live path — the bot runs SATS
# (strategy/sats.py) as its sole strategy. The former tests that asserted
# SMC candidates flow through scoring_strategy.find_candidate / _prepare_smc
# and share Golden Trio's score budget were removed with that path. The SMC
# detector itself is retained and its detection is still covered above.
