"""Tests for strategy/golden_trio.py — the sole signal source for the gold-only bot."""
from strategy.golden_trio import find_golden_trio_candidate
from tests.helpers import make_candles


def _flat_candles(n, price=2000.0):
    out = []
    for _ in range(n):
        out.append({"o": price, "h": price + 0.5, "l": price - 0.5, "c": price, "v": None})
    return out


def _long_setup_candles():
    """Steep uptrend so ZLSMA(30) stays clearly aligned up over the last 15
    bars, then a quick pullback drives RSI below the dip level, then a
    smaller recovery bar, then the trigger crosses RSI back through 50 with
    a bullish body and low that hugs the Turtle lower band."""
    candles = make_candles(300, start_price=1800.0, step=20.0, noise=3.0)
    price = candles[-1]["c"]
    # 6-bar deep pullback: drives RSI below 45, and ZLSMA slope over the
    # 15-bar window still stays up thanks to the very steep prior uptrend.
    for _ in range(6):
        c_next = price - 45.0
        candles.append({"o": price, "h": price + 0.3, "l": c_next - 0.3, "c": c_next, "v": None})
        price = c_next
    # Recovery bar so bar[-2] RSI is climbing (satisfies the "some climb"
    # gate); needs to raise the pre-trigger low but not overshoot.
    recovery_close = price + 25.0
    candles.append({"o": price, "h": recovery_close + 0.3, "l": price - 0.3, "c": recovery_close, "v": None})
    price = recovery_close
    # Trigger: bigger bullish body, long lower wick tests the band.
    trigger_close = price + 100.0
    trigger_low = price - 40.0
    candles.append({"o": price, "h": trigger_close + 0.5, "l": trigger_low, "c": trigger_close, "v": None})
    return candles


def _short_setup_candles():
    candles = make_candles(300, start_price=7800.0, step=-20.0, noise=3.0)
    price = candles[-1]["c"]
    for _ in range(6):
        c_next = price + 45.0
        candles.append({"o": price, "h": c_next + 0.3, "l": price - 0.3, "c": c_next, "v": None})
        price = c_next
    recovery_close = price - 25.0
    candles.append({"o": price, "h": price + 0.3, "l": recovery_close - 0.3, "c": recovery_close, "v": None})
    price = recovery_close
    trigger_close = price - 100.0
    trigger_high = price + 40.0
    candles.append({"o": price, "h": trigger_high, "l": trigger_close - 0.5, "c": trigger_close, "v": None})
    return candles


def test_returns_none_when_not_enough_bars():
    assert find_golden_trio_candidate(_flat_candles(10)) is None


def test_returns_none_on_flat_market():
    # Flat RSI hovers around 50 and range is compressed -> chop veto also kills it.
    assert find_golden_trio_candidate(_flat_candles(200)) is None


def test_long_signal_fires_when_all_gates_align():
    result = find_golden_trio_candidate(_long_setup_candles())
    assert result is not None
    assert result["direction"] == "BUY"
    assert result["pattern"] == "GOLDEN_TRIO"
    assert result["stop_loss"] < result["entry_price"] < result["tp1"] < result["tp2"] <= result["tp3"]
    assert result["risk"] > 0
    assert 0 <= result["rsi_quality"] <= 30
    assert 0 <= result["turtle_quality"] <= 20
    assert result["zlsma_status"] in ("aligned", "flat")


def test_short_signal_fires_when_all_gates_align():
    result = find_golden_trio_candidate(_short_setup_candles())
    assert result is not None
    assert result["direction"] == "SELL"
    assert result["tp3"] <= result["tp2"] < result["tp1"] < result["entry_price"] < result["stop_loss"]


def test_candidate_carries_diagnostic_indicator_values():
    result = find_golden_trio_candidate(_long_setup_candles())
    for k in ("rsi", "zlsma", "turtle_upper", "turtle_lower", "atr", "zlsma_status", "rsi_quality", "turtle_quality"):
        assert k in result, f"missing diagnostic key {k!r}"
