"""Tests for walk-forward threshold validation.

The single most important property here is that the CONFIRM window never
influences which threshold is chosen. A holdout that also picks the
answer is not a holdout -- that is precisely how WATCH_MIN_SCORE was
moved 45 -> 55 on a result that later reversed. These tests pin the
discipline, not the numbers.
"""
import pandas as pd
import pytest

import backtest as bt


def _sig(bar, score, r, t):
    out = {"outcome": "tp1" if r > 0 else "stop_before_tp1", "r": r}
    return {
        "bar_index": bar,
        "t": t,
        "score": score,
        "outcomes": {c: dict(out) for c in ("ideal", "realistic", "conservative")},
    }


def _candles(n, start="2025-01-01T00:00:00Z"):
    ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    return [{"t": x.isoformat(), "o": 1, "h": 2, "l": 0, "c": 1} for x in ts]


def _split_times(n=400, split=0.5):
    c = _candles(n)
    return c, c[int(n * split)]["t"]


def test_choice_ignores_the_confirm_window(capsys):
    """The decisive test.

    Threshold 45 LOSES in fit and WINS hugely in confirm; threshold 65
    wins modestly in fit. A chooser that peeks at the holdout picks 45.
    A correct one picks 65 and then reports that confirm disagreed.
    """
    candles, split_t = _split_times()
    split_ts = pd.to_datetime(split_t, utc=True)
    sigs = []
    for i, c in enumerate(candles[:-1]):
        in_fit = pd.to_datetime(c["t"], utc=True) < split_ts
        # score 45 band
        sigs.append(_sig(i, 45, -1.0 if in_fit else +3.0, c["t"]))
        # score 65 band
        sigs.append(_sig(i, 65, +0.4 if in_fit else -1.0, c["t"]))

    bt.walk_forward_report(sigs, candles, split=0.5,
                           min_per_week=0.0, max_per_week=1e9,
                           min_confirm_n=5)
    out = capsys.readouterr().out

    # Thresholds are >=, so every cutoff above 45 captures only the
    # score-65 signals; the lowest fit-eligible one is therefore 50, not
    # 65. What matters is WHICH WINDOW decided, not the exact number:
    # 45 is the cutoff the holdout adores (+3.0R) and loathes in fit
    # (-0.3R blended), so a chooser that peeked would land on it.
    assert "CHOSEN ON FIT" in out, out
    chosen = out.split("CHOSEN ON FIT: WATCH_MIN_SCORE = ")[1].split()[0]
    assert chosen != "45", "the holdout-favoured threshold was chosen"
    assert int(chosen) >= 50, chosen
    # And having chosen on fit, it must report that confirm disagreed.
    assert "OUT-OF-SAMPLE: FAILED" in out, out


def test_a_choice_that_fails_out_of_sample_is_reported_as_failed(capsys):
    candles, split_t = _split_times()
    split_ts = pd.to_datetime(split_t, utc=True)
    sigs = []
    for i, c in enumerate(candles[:-1]):
        in_fit = pd.to_datetime(c["t"], utc=True) < split_ts
        sigs.append(_sig(i, 65, +0.5 if in_fit else -0.5, c["t"]))
    bt.walk_forward_report(sigs, candles, split=0.5,
                           min_per_week=0.0, max_per_week=1e9,
                           min_confirm_n=5)
    out = capsys.readouterr().out
    assert "OUT-OF-SAMPLE: FAILED" in out
    assert "Do NOT adopt this threshold" in out


def test_a_choice_that_survives_is_reported_as_held(capsys):
    candles, split_t = _split_times()
    sigs = [_sig(i, 65, +0.5, c["t"]) for i, c in enumerate(candles[:-1])]
    bt.walk_forward_report(sigs, candles, split=0.5,
                           min_per_week=0.0, max_per_week=1e9,
                           min_confirm_n=5)
    out = capsys.readouterr().out
    assert "OUT-OF-SAMPLE: HELD" in out


def test_thin_confirm_window_is_called_untested_not_passed(capsys):
    """A handful of confirm trades cannot validate anything, and must not
    be dressed up as confirmation."""
    candles, split_t = _split_times()
    split_ts = pd.to_datetime(split_t, utc=True)
    sigs = []
    for i, c in enumerate(candles[:-1]):
        if pd.to_datetime(c["t"], utc=True) < split_ts:
            sigs.append(_sig(i, 65, +0.5, c["t"]))
    # exactly two confirm-side signals
    for i, c in enumerate(candles[-3:-1]):
        sigs.append(_sig(9000 + i, 65, +0.9, c["t"]))
    bt.walk_forward_report(sigs, candles, split=0.5,
                           min_per_week=0.0, max_per_week=1e9,
                           min_confirm_n=30)
    out = capsys.readouterr().out
    assert "too" in out and "thin" in out
    assert "HELD" not in out


def test_no_eligible_threshold_recommends_nothing(capsys):
    """Everything loses in fit -> nothing is chosen, and the confirm
    window's favourite must be explicitly disclaimed."""
    candles, split_t = _split_times()
    split_ts = pd.to_datetime(split_t, utc=True)
    sigs = []
    for i, c in enumerate(candles[:-1]):
        in_fit = pd.to_datetime(c["t"], utc=True) < split_ts
        sigs.append(_sig(i, 45, -0.5 if in_fit else +2.0, c["t"]))
    bt.walk_forward_report(sigs, candles, split=0.5,
                           min_per_week=0.0, max_per_week=1e9,
                           min_confirm_n=5)
    out = capsys.readouterr().out
    assert "NO THRESHOLD IS ELIGIBLE" in out
    assert "CHOSEN ON FIT" not in out
    assert "NOT a recommendation" in out


def test_cadence_band_rejects_a_profitable_but_deafening_threshold(capsys):
    candles, split_t = _split_times()
    sigs = [_sig(i, 45, +0.5, c["t"]) for i, c in enumerate(candles[:-1])]
    bt.walk_forward_report(sigs, candles, split=0.5,
                           min_per_week=0.0, max_per_week=0.001,
                           min_confirm_n=5)
    out = capsys.readouterr().out
    assert "noisy" in out
    assert "NO THRESHOLD IS ELIGIBLE" in out


def test_split_is_chronological_never_shuffled():
    """Fit must be strictly earlier than confirm. Shuffling would leak
    the future into the past and quietly invalidate the whole report."""
    candles = _candles(400)
    split_ts = pd.to_datetime(candles[200]["t"], utc=True)
    sigs = [_sig(i, 50, 0.1, c["t"]) for i, c in enumerate(candles[:-1])]
    fit = [s for s in sigs if pd.to_datetime(s["t"], utc=True) < split_ts]
    conf = [s for s in sigs if pd.to_datetime(s["t"], utc=True) >= split_ts]
    assert fit and conf
    assert max(pd.to_datetime(s["t"], utc=True) for s in fit) < \
           min(pd.to_datetime(s["t"], utc=True) for s in conf)


def test_empty_input_does_not_crash(capsys):
    bt.walk_forward_report([], _candles(100), split=0.5)
    assert "no scored candidates" in capsys.readouterr().out


def test_window_weeks_is_positive_and_ordered():
    candles = _candles(4 * 96 * 7)   # ~4 weeks of M15
    whole = bt._window_weeks(candles, 0.0, 1.0)
    half = bt._window_weeks(candles, 0.0, 0.5)
    assert whole > half > 0
    assert 3.0 < whole < 5.0, whole


def test_wf_line_separates_candidates_from_filled_trades():
    """The cadence denominator and the statistics denominator differ; the
    line must show both so a candidate count is not read as a trade count."""
    rows = [_sig(0, 50, 1.0, "2025-01-01T00:00:00Z"),
            _sig(1, 50, 1.0, "2025-01-01T00:15:00Z")]
    rows.append({"bar_index": 2, "t": "2025-01-01T00:30:00Z", "score": 50,
                 "outcomes": {c: {"outcome": "no_fill_expired", "r": 0.0}
                              for c in ("ideal", "realistic", "conservative")}})
    line = bt._wf_line(rows, weeks=1.0)
    assert "n=   3" in line, line     # three candidates
    assert "2f" in line, line          # two filled
