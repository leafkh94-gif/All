"""Tests for strategy/golden_trio.py — the sole signal source for the gold-only bot."""
from strategy.golden_trio import find_golden_trio_candidate
from tests.helpers import make_candles


def _flat_candles(n, price=2000.0):
    out = []
    for _ in range(n):
        out.append({"o": price, "h": price + 0.5, "l": price - 0.5, "c": price, "v": None})
    return out


def _long_setup_candles():
    """Steep noisy uptrend (RSI mixed ~55, ZLSMA rises fast), then a very
    short + sharp pullback that stamps a new Donchian low + drives RSI < 20
    without dominating the 20-bar ZLSMA slope window, then a bullish
    reversal trigger with a long lower wick tagging the band."""
    candles = make_candles(300, start_price=1800.0, step=6.0, noise=3.0)
    price = candles[-1]["c"]
    for _ in range(8):
        c_next = price - 30.0
        candles.append({"o": price, "h": price + 0.3, "l": c_next - 0.3, "c": c_next, "v": None})
        price = c_next
    trigger_close = price + 80.0
    trigger_low = price - 0.5
    candles.append({"o": price, "h": trigger_close + 0.5, "l": trigger_low, "c": trigger_close, "v": None})
    return candles


def _short_setup_candles():
    # Exact mirror of the long setup magnitudes.
    candles = make_candles(300, start_price=3600.0, step=-6.0, noise=3.0)
    price = candles[-1]["c"]
    for _ in range(8):
        c_next = price + 35.0
        candles.append({"o": price, "h": c_next + 0.3, "l": price - 0.3, "c": c_next, "v": None})
        price = c_next
    trigger_close = price - 110.0
    trigger_high = price + 0.5
    candles.append({"o": price, "h": trigger_high, "l": trigger_close - 0.5, "c": trigger_close, "v": None})
    return candles


def test_returns_none_when_not_enough_bars():
    assert find_golden_trio_candidate(_flat_candles(10)) is None


def test_returns_none_on_flat_market():
    # Flat RSI hovers around 50, so oversold-dip gate never triggers.
    assert find_golden_trio_candidate(_flat_candles(200)) is None


def test_long_signal_fires_when_all_gates_align():
    result = find_golden_trio_candidate(_long_setup_candles())
    assert result is not None
    assert result["direction"] == "BUY"
    assert result["pattern"] == "GOLDEN_TRIO"
    assert result["stop_loss"] < result["entry_price"] < result["tp1"] < result["tp2"] <= result["tp3"]
    assert result["risk"] > 0
    assert 0 <= result["quality"] <= result["quality_max"]


def test_short_signal_fires_when_all_gates_align():
    result = find_golden_trio_candidate(_short_setup_candles())
    assert result is not None
    assert result["direction"] == "SELL"
    assert result["tp3"] < result["tp2"] < result["tp1"] < result["entry_price"] < result["stop_loss"]


def test_candidate_carries_diagnostic_indicator_values():
    result = find_golden_trio_candidate(_long_setup_candles())
    for k in ("rsi", "zlsma", "turtle_upper", "turtle_lower", "atr"):
        assert k in result, f"missing diagnostic key {k!r}"
