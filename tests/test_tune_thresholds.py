"""Tests for the cadence-based threshold tuner.

Its whole job is to stop thresholds being set by guesswork, so the
properties that matter are: it reads the delivered rate (not the raw
candidate count), it refuses to be misled by a censored log, and raising
a threshold never increases the rate.
"""
import importlib.util
import os

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools", "tune_thresholds.py")
_spec = importlib.util.spec_from_file_location("tune_thresholds", _PATH)
tt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tt)


def _sig(score, day, tradeable=True, reason=None):
    return {"t": f"2025-01-{day:02d}T12:00:00+00:00", "score": score,
            "tradeable": tradeable, "suppressed_reason": reason,
            "outcomes": {"realistic": {"outcome": "tp3_runner_complete", "r": 1.0}}}


def _week(scores, tradeable=True, reason=None):
    """One week of signals spread over 7 days."""
    return [_sig(s, 1 + (i % 7), tradeable, reason) for i, s in enumerate(scores)]


def test_span_weeks_measures_the_actual_window():
    sigs = [_sig(50, 1), _sig(50, 15)]
    assert 1.9 < tt.span_weeks(sigs) < 2.1


def test_delivered_rate_counts_only_undelivered_suppression_out():
    """A row suppressed by an open position is not an alert the user got,
    so it must not inflate the delivered rate."""
    sigs = _week([80] * 10, tradeable=True) + _week([80] * 10, tradeable=False,
                                                    reason="open_position")
    weeks = tt.span_weeks(sigs)
    aplus, _watch, _elig = tt.delivered_at(sigs, 45, 70, weeks)
    assert aplus * weeks == 10          # the 10 suppressed ones don't count


def test_below_threshold_rows_become_deliverable_when_the_bar_drops():
    """That is the entire point of --record-all: a candidate that scored 60
    under a 70 threshold is a real alert once the threshold is 55."""
    sigs = _week([60] * 8, tradeable=False, reason="below_threshold")
    weeks = tt.span_weeks(sigs)
    at70, _, _ = tt.delivered_at(sigs, 45, 70, weeks)
    at55, _, _ = tt.delivered_at(sigs, 45, 55, weeks)
    assert at70 == 0
    assert at55 * weeks == 8


def test_raising_the_aplus_threshold_never_raises_the_aplus_rate():
    sigs = _week(list(range(45, 85)))
    weeks = tt.span_weeks(sigs)
    rates = [tt.delivered_at(sigs, 45, a, weeks)[0] for a in (55, 60, 65, 70, 75)]
    assert rates == sorted(rates, reverse=True)


def test_pick_moves_the_threshold_toward_the_requested_cadence():
    """A demanding target should land on a lower bar than a sparse one."""
    sigs = _week(list(range(45, 90)) * 3)
    weeks = tt.span_weeks(sigs)
    _c, _w_many, a_many, _ar, _wr = tt.pick(sigs, 30.0, 60.0, weeks)
    _c, _w_few, a_few, _ar, _wr = tt.pick(sigs, 2.0, 10.0, weeks)
    assert a_many <= a_few


def test_pick_keeps_watch_below_aplus():
    sigs = _week(list(range(45, 90)) * 2)
    weeks = tt.span_weeks(sigs)
    _c, w, a, _ar, _wr = tt.pick(sigs, 5.0, 20.0, weeks)
    assert w < a
