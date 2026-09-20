"""Tests for the entry-mode head-to-head report.

The point of this tool is to stop a difference that is really noise from
being quoted as a result, so the tests are mostly about it refusing to
overclaim.
"""
import importlib.util
import json
import os

import pytest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools", "compare_entry_modes.py")
_spec = importlib.util.spec_from_file_location("compare_entry_modes", _PATH)
cmp_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cmp_mod)


def _row(bar, r, mode="REVERSION", suppressed=None):
    out = {"outcome": "tp1" if r > 0 else "stop_before_tp1", "r": r}
    return {
        "bar_index": bar,
        "entry_mode": mode,
        "suppressed_reason": suppressed,
        "outcomes": {c: dict(out) for c in ("ideal", "realistic", "conservative")},
    }


def _nofill(bar, mode="REVERSION"):
    out = {"outcome": "no_fill_expired", "r": None}
    return {
        "bar_index": bar,
        "entry_mode": mode,
        "suppressed_reason": None,
        "outcomes": {c: dict(out) for c in ("ideal", "realistic", "conservative")},
    }


def test_no_fill_rows_are_excluded():
    rows = [_row(1, 1.0), _nofill(2), _row(3, -1.0)]
    assert len(cmp_mod._fills(rows, "ideal")) == 2
    s = cmp_mod._stats(rows, "ideal")
    assert s["n"] == 2


def test_sub_threshold_rows_are_excluded(tmp_path):
    """Sub-threshold candidates are distribution data, not alerts.
    Including them would compare two different populations."""
    rows = [_row(1, 1.0), _row(2, 5.0, suppressed="below_threshold")]
    p = tmp_path / "s.json"
    p.write_text(json.dumps(rows))
    loaded = cmp_mod._load(str(p))
    assert len(loaded) == 1
    assert loaded[0]["bar_index"] == 1


def test_stats_reports_win_rate_and_profit_factor():
    rows = [_row(i, 1.0) for i in range(3)] + [_row(i + 10, -1.0) for i in range(1)]
    s = cmp_mod._stats(rows, "ideal")
    assert s["n"] == 4
    assert s["wr"] == pytest.approx(0.75)
    assert s["pf"] == pytest.approx(3.0)
    assert s["mean"] == pytest.approx(0.5)


def test_identical_inputs_produce_zero_difference():
    a = [_row(i, 0.5 if i % 2 else -1.0) for i in range(40)]
    sa = cmp_mod._stats(a, "ideal")
    diff, t, _p = cmp_mod._welch(sa, sa)
    assert diff == pytest.approx(0.0)
    assert t == pytest.approx(0.0)


def test_paired_uses_only_shared_bars():
    a = [_row(1, 1.0), _row(2, 1.0), _row(3, 1.0)]
    b = [_row(2, 2.0, "MOMENTUM"), _row(3, 2.0, "MOMENTUM"),
         _row(99, 50.0, "MOMENTUM")]
    pr = cmp_mod._paired(a, b, "ideal")
    assert pr["n"] == 2, "bar 1 and bar 99 are not shared"
    # +1.0 on each shared bar; the 50.0 outlier on an unshared bar must
    # not leak in.
    assert pr["mean"] == pytest.approx(1.0)


def test_paired_ignores_unfilled_bars():
    """A bar only counts as shared when BOTH arms actually filled it."""
    a = [_row(1, 1.0), _row(2, 1.0), _nofill(3)]
    b = [_row(1, 2.0, "MOMENTUM"), _row(2, 2.0, "MOMENTUM"),
         _row(3, 9.0, "MOMENTUM")]
    pr = cmp_mod._paired(a, b, "ideal")
    assert pr["n"] == 2, "bar 3 did not fill in arm A, so it is not shared"
    assert pr["mean"] == pytest.approx(1.0)


def test_paired_needs_two_points_for_an_error_bar():
    """One shared bar cannot produce a standard error; return None rather
    than a difference with no uncertainty attached."""
    a = [_row(1, 1.0), _nofill(2)]
    b = [_row(1, 2.0, "MOMENTUM"), _row(2, 9.0, "MOMENTUM")]
    assert cmp_mod._paired(a, b, "ideal") is None


def test_paired_returns_none_without_enough_overlap():
    a = [_row(1, 1.0)]
    b = [_row(2, 1.0, "MOMENTUM")]
    assert cmp_mod._paired(a, b, "ideal") is None


def test_welch_flags_a_real_difference():
    a = [_row(i, -1.0) for i in range(200)]
    b = [_row(i, 1.0, "MOMENTUM") for i in range(200)]
    sa, sb = cmp_mod._stats(a, "ideal"), cmp_mod._stats(b, "ideal")
    diff, t, p = cmp_mod._welch(sa, sb)
    assert diff == pytest.approx(2.0)
    # Zero variance in both arms -> se 0 -> t is undefined, not infinite
    # nonsense. Guarded rather than crashing.
    assert t is None or abs(t) > 2


def test_welch_is_unimpressed_by_noise():
    """Two samples drawn from the same noisy process must not register."""
    import random
    rnd = random.Random(4)
    a = [_row(i, rnd.choice([-1.0, 2.0])) for i in range(400)]
    b = [_row(i, rnd.choice([-1.0, 2.0]), "MOMENTUM") for i in range(400)]
    sa, sb = cmp_mod._stats(a, "ideal"), cmp_mod._stats(b, "ideal")
    _diff, t, _p = cmp_mod._welch(sa, sb)
    assert abs(t) < 2, f"noise registered as signal (t={t:+.2f})"


def test_empty_arm_does_not_crash():
    rows = [_nofill(1), _nofill(2)]
    assert cmp_mod._stats(rows, "ideal") is None
    diff, t, p = cmp_mod._welch(None, None)
    assert diff is None and t is None and p is None
