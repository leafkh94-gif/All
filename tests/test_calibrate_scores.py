"""Tests for the score-calibration tool.

The tool's job is to refuse to overclaim. Most of these tests check that
it says "not enough data" when there isn't enough data, because that is
the failure mode that turns a backtest into a marketing number.
"""
import importlib.util
import os

MODULE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "tools", "calibrate_scores.py")
_spec = importlib.util.spec_from_file_location("calibrate_scores", MODULE_PATH)
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)


def _sig(score, r, outcome="tp3_runner_complete", pattern="GOLDEN_TRIO",
         t="2025-01-01T00:00:00+00:00", tradeable=True, breakdown=None):
    return {
        "t": t, "score": score, "pattern": pattern, "tradeable": tradeable,
        "breakdown": breakdown or {},
        "outcomes": {"realistic": {"outcome": outcome, "r": r},
                     "ideal": {"outcome": outcome, "r": r},
                     "conservative": {"outcome": outcome, "r": r}},
    }


# ─── stats ───────────────────────────────────────────────────────────

def test_stats_excludes_unfilled_signals_from_expectancy():
    signals = [_sig(60, 1.0), _sig(60, -1.0),
               _sig(60, 0.0, outcome="no_fill_expired")]
    st = cal.stats(signals)
    assert st["n"] == 2          # the unfilled one doesn't dilute the mean
    assert st["n_raw"] == 3
    assert st["avg_r"] == 0.0


def test_stats_reports_a_standard_error_so_claims_carry_an_error_bar():
    signals = [_sig(60, r) for r in (1.0, -1.0, 2.0, -0.5, 0.3)]
    st = cal.stats(signals)
    assert st["se"] > 0


def test_stats_on_an_empty_set_is_zero_not_an_error():
    st = cal.stats([])
    assert st["n"] == 0 and st["avg_r"] == 0.0


# ─── monotonicity verdict ────────────────────────────────────────────

def test_monotonicity_refuses_to_judge_an_underpowered_sample():
    rows = [((45, 54), {"n": 5, "avg_r": 0.1}), ((55, 64), {"n": 4, "avg_r": 0.5})]
    verdict, why = cal.monotonicity(rows)
    assert verdict == "insufficient"
    assert str(cal.MIN_BUCKET_N) in why


def test_monotonicity_detects_a_score_that_orders_outcomes():
    rows = [((45, 54), {"n": 50, "avg_r": -0.2}),
            ((55, 64), {"n": 50, "avg_r": 0.0}),
            ((65, 74), {"n": 50, "avg_r": 0.3})]
    verdict, _ = cal.monotonicity(rows)
    assert verdict == "monotonic"


def test_monotonicity_detects_a_score_that_does_not_order_outcomes():
    rows = [((45, 54), {"n": 50, "avg_r": 0.3}),
            ((55, 64), {"n": 50, "avg_r": -0.2}),
            ((65, 74), {"n": 50, "avg_r": 0.1}),
            ((75, 84), {"n": 50, "avg_r": -0.4})]
    verdict, _ = cal.monotonicity(rows)
    assert verdict == "not monotonic"


def test_underpowered_buckets_cannot_carry_a_monotonic_verdict():
    """A tidy-looking trend held up by a 3-signal bucket is not a trend."""
    rows = [((45, 54), {"n": 100, "avg_r": -0.2}),
            ((55, 64), {"n": 3, "avg_r": 0.0}),
            ((65, 74), {"n": 2, "avg_r": 0.9})]
    verdict, _ = cal.monotonicity(rows)
    assert verdict == "insufficient"


# ─── formatting flags small samples ──────────────────────────────────

def test_fmt_flags_buckets_below_the_minimum_sample_size():
    small = cal.stats([_sig(60, 1.0)])
    big = cal.stats([_sig(60, 1.0) for _ in range(cal.MIN_BUCKET_N + 5)])
    assert "under-powered" in cal.fmt(small)
    assert "under-powered" not in cal.fmt(big)


def test_bucket_of_maps_scores_into_the_default_bands():
    assert cal.bucket_of(50, cal.DEFAULT_BUCKETS) == (45, 54)
    assert cal.bucket_of(100, cal.DEFAULT_BUCKETS) == (85, 100)
    assert cal.bucket_of(-5, cal.DEFAULT_BUCKETS) is None
