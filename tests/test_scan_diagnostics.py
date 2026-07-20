import datetime as dt

from strategy import scan_diagnostics as diag


def _candle(t, o=100.0, h=101.0, l=99.0, c=100.5, v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def test_bars_report_empty_data():
    report = diag.bars_report("US500", [])
    assert "0 bars" in report
    assert "feed returned nothing" in report


def test_bars_report_none_data():
    report = diag.bars_report("US500", None)
    assert "0 bars" in report


def test_bars_report_too_few_bars_is_data_problem():
    candles = [_candle(f"2026-01-01T00:{i:02d}:00") for i in range(10)]
    report = diag.bars_report("US500", candles)
    assert "10/30 bars" in report
    assert "data problem" in report
    assert "short by 20" in report


def test_bars_report_stale_feed():
    # 30 fresh-looking bars, but the last candle is far in the past relative to now_utc
    candles = [_candle(f"2026-01-01T00:{i:02d}:00") for i in range(30)]
    now = dt.datetime(2026, 1, 1, 5, 0, tzinfo=dt.timezone.utc)  # hours after last candle
    report = diag.bars_report("US500", candles, now_utc=now)
    assert "stale feed" in report
    assert "data problem" in report


def test_bars_report_ok_fresh_data_means_detectors_too_tight():
    candles = [_candle(f"2026-01-01T00:{i:02d}:00") for i in range(30)]
    now = dt.datetime(2026, 1, 1, 0, 30, tzinfo=dt.timezone.utc)  # 1 min after last candle (00:29)
    report = diag.bars_report("US500", candles, now_utc=now)
    assert "detectors too tight" in report
    assert "stale" not in report


def test_bars_report_future_timestamp_reports_inconsistent_not_fresh():
    # Reproduces the observed anomaly: a last-candle timestamp after now_utc
    # is physically impossible and must not be asserted as "fresh".
    candles = [_candle(f"2026-01-01T04:{i:02d}:00") for i in range(30)]
    now = dt.datetime(2026, 1, 1, 0, 30, tzinfo=dt.timezone.utc)  # hours before the last candle
    report = diag.bars_report("BTCUSD", candles, now_utc=now)
    assert "inconsistent" in report
    assert "freshness unverified" in report
    assert "detectors too tight" not in report
    assert "stale feed" not in report


def test_bars_report_never_raises_on_malformed_candles():
    # no "t" key at all — timestamp lookup should quietly give up, not raise
    candles = [{"o": 1, "h": 2, "l": 0, "c": 1} for _ in range(30)]
    report = diag.bars_report("US500", candles, now_utc=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    assert "US500" in report


def test_is_data_problem_true_for_missing_bars():
    assert diag.is_data_problem(diag.bars_report("US500", [])) is True


def test_is_data_problem_true_for_too_few_bars():
    candles = [_candle(f"2026-01-01T00:{i:02d}:00") for i in range(10)]
    assert diag.is_data_problem(diag.bars_report("US500", candles)) is True


def test_is_data_problem_true_for_stale_feed():
    candles = [_candle(f"2026-01-01T00:{i:02d}:00") for i in range(30)]
    now = dt.datetime(2026, 1, 1, 5, 0, tzinfo=dt.timezone.utc)
    assert diag.is_data_problem(diag.bars_report("US500", candles, now_utc=now)) is True


def test_is_data_problem_false_for_fresh_data():
    candles = [_candle(f"2026-01-01T00:{i:02d}:00") for i in range(30)]
    now = dt.datetime(2026, 1, 1, 0, 30, tzinfo=dt.timezone.utc)
    assert diag.is_data_problem(diag.bars_report("US500", candles, now_utc=now)) is False


def test_is_data_problem_false_for_unverified_freshness():
    """A weird-but-not-necessarily-wrong timestamp (the inconsistent-age
    case) still has real bars present -- don't hard-block scoring over it,
    only genuine data problems."""
    candles = [_candle(f"2026-01-01T04:{i:02d}:00") for i in range(30)]
    now = dt.datetime(2026, 1, 1, 0, 30, tzinfo=dt.timezone.utc)
    assert diag.is_data_problem(diag.bars_report("BTCUSD", candles, now_utc=now)) is False
