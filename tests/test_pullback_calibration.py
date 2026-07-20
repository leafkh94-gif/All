import strategy.pullback_calibration as calib


def _flat(n, price=100.0):
    return [{"t": f"2026-01-01T00:{i:02d}:00", "o": price, "h": price, "l": price, "c": price, "v": None}
            for i in range(n)]


def _measure_scenario():
    """5 flat padding candles, one BUY breakout candle (leg_extreme=95,
    close=100 -> leg_size=5), then 4 future candles whose lows step down by
    exactly one tested retrace depth each bar, deterministically exercising
    fill-rate/time-to-fill/depth-reached math."""
    candles = _flat(5)
    candles.append({"t": "2026-01-01T01:00:00", "o": 100.0, "h": 100.2, "l": 94.0, "c": 100.0, "v": None})
    candles.append({"t": "2026-01-01T01:15:00", "o": 100.0, "h": 100.1, "l": 98.4, "c": 98.6, "v": None})  # fills 0.30
    candles.append({"t": "2026-01-01T01:30:00", "o": 98.6, "h": 98.7, "l": 97.8, "c": 98.0, "v": None})    # fills 0.40
    candles.append({"t": "2026-01-01T01:45:00", "o": 98.0, "h": 98.1, "l": 97.2, "c": 97.5, "v": None})    # fills 0.50
    candles.append({"t": "2026-01-01T02:00:00", "o": 97.5, "h": 97.6, "l": 96.8, "c": 97.0, "v": None})    # fills 0.60
    breakout = {"index": 5, "candidate": {"direction": "BUY", "leg_extreme": 95.0, "sweep_price": 95.0}}
    return candles, breakout


def test_measure_breakout_depth_reached_and_fill_results():
    candles, breakout = _measure_scenario()
    result = calib.measure_breakout(candles, breakout, lookahead=10)
    assert result is not None
    assert result["depth_reached"] == (100.0 - 96.8) / 5.0
    assert result["fill_results"][0.30] == 1
    assert result["fill_results"][0.40] == 2
    assert result["fill_results"][0.50] == 3
    assert result["fill_results"][0.60] == 4
    assert result["fill_results"][0.70] is None  # never reached within the provided future candles
    assert result["tagged_fvg"] is False


def test_measure_breakout_degenerate_leg_returns_none():
    candles = _flat(5)
    candles.append({"t": "2026-01-01T01:00:00", "o": 100.0, "h": 100.2, "l": 99.8, "c": 100.0, "v": None})
    breakout = {"index": 5, "candidate": {"direction": "BUY", "leg_extreme": 105.0, "sweep_price": 105.0}}
    assert calib.measure_breakout(candles, breakout) is None


def test_measure_breakout_no_future_candles_returns_none():
    candles, breakout = _measure_scenario()
    breakout = {"index": len(candles) - 1, "candidate": breakout["candidate"]}
    assert calib.measure_breakout(candles, breakout) is None


def test_calibrate_instrument_returns_none_without_breakouts():
    class _FakeFeed:
        def get_candles(self, instrument, interval, n=60):
            return _flat(20)  # too short for any detector to fire, and < MIN_HISTORY_BARS

    assert calib.calibrate_instrument(_FakeFeed(), "US500", n=20) is None


def test_format_report_includes_recommended_retrace_pct():
    fake_result = {
        "instrument": "US500", "sample_size": 42,
        "depth_percentiles": {25: 0.2, 50: 0.4, 75: 0.6, 90: 0.8},
        "fvg_tag_rate": 0.3,
        "fill_stats": {pct: {"fill_rate": 0.6, "avg_bars_to_fill": 3.0} for pct in calib.RETRACE_DEPTHS_TO_TEST},
        "recommended_retrace_pct": 0.40,
    }
    report = calib.format_report([fake_result, None])
    assert "US500" in report
    assert "Recommended retrace_pct: 0.40" in report
    assert "RECOMMENDATIONS ONLY" in report
