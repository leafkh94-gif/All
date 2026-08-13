import datetime as dt

from strategy import scan_diagnostics as diag


def _candle(t, o=100.0, h=101.0, l=99.0, c=100.5, v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _fresh_bars(n=120):
    # 15-min bars starting 2026-01-01T00:00; last bar is at bar n-1.
    return [_candle((dt.datetime(2026, 1, 1) + dt.timedelta(minutes=15 * i)).isoformat()) for i in range(n)]


def test_bars_report_empty_data():
    report = diag.bars_report("XAUUSD", [])
    assert "0 bars" in report
    assert "feed returned nothing" in report


def test_bars_report_none_data():
    report = diag.bars_report("XAUUSD", None)
    assert "0 bars" in report


def test_bars_report_too_few_bars_is_data_problem():
    candles = _fresh_bars(10)
    report = diag.bars_report("XAUUSD", candles)
    assert "10/70 bars" in report
    assert "data problem" in report
    assert "short by 60" in report


def test_bars_report_stale_feed():
    candles = _fresh_bars(120)
    # last candle is at 00:00 + 15*119 min = ~29h45m; make now much later
    now = dt.datetime(2026, 1, 5, 12, 0, tzinfo=dt.timezone.utc)
    report = diag.bars_report("XAUUSD", candles, now_utc=now)
    assert "stale feed" in report
    assert "data problem" in report


def test_bars_report_ok_fresh_data_means_detectors_too_tight():
    candles = _fresh_bars(120)
    last_ts = dt.datetime.fromisoformat(candles[-1]["t"])
    now = last_ts + dt.timedelta(minutes=1)
    report = diag.bars_report("XAUUSD", candles, now_utc=now.replace(tzinfo=dt.timezone.utc))
    assert "detectors too tight" in report
    assert "stale" not in report


def test_bars_report_future_timestamp_reports_inconsistent_not_fresh():
    candles = _fresh_bars(120)
    now = dt.datetime(2025, 12, 31, 0, 0, tzinfo=dt.timezone.utc)
    report = diag.bars_report("XAUUSD", candles, now_utc=now)
    assert "inconsistent" in report
    assert "freshness unverified" in report
    assert "detectors too tight" not in report
    assert "stale feed" not in report


def test_bars_report_never_raises_on_malformed_candles():
    candles = [{"o": 1, "h": 2, "l": 0, "c": 1} for _ in range(120)]
    report = diag.bars_report("XAUUSD", candles, now_utc=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    assert "XAUUSD" in report


def test_is_data_problem_true_for_missing_bars():
    assert diag.is_data_problem(diag.bars_report("XAUUSD", [])) is True


def test_is_data_problem_true_for_too_few_bars():
    candles = _fresh_bars(10)
    assert diag.is_data_problem(diag.bars_report("XAUUSD", candles)) is True


def test_is_data_problem_true_for_stale_feed():
    candles = _fresh_bars(120)
    now = dt.datetime(2026, 1, 5, 12, 0, tzinfo=dt.timezone.utc)
    assert diag.is_data_problem(diag.bars_report("XAUUSD", candles, now_utc=now)) is True


def test_is_data_problem_false_for_fresh_data():
    candles = _fresh_bars(120)
    last_ts = dt.datetime.fromisoformat(candles[-1]["t"]).replace(tzinfo=dt.timezone.utc)
    now = last_ts + dt.timedelta(minutes=1)
    assert diag.is_data_problem(diag.bars_report("XAUUSD", candles, now_utc=now)) is False


def test_is_data_problem_false_for_unverified_freshness():
    candles = _fresh_bars(120)
    now = dt.datetime(2025, 12, 31, 0, 0, tzinfo=dt.timezone.utc)
    assert diag.is_data_problem(diag.bars_report("XAUUSD", candles, now_utc=now)) is False
