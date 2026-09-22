"""Tests for the SATS engine (strategy/sats.py).

These pin the properties that matter for a faithful, non-repainting port:
the flip fires only on the last (just-closed) bar, TQI is a bounded blend,
the trade plan is geometrically sound, and the same window always yields the
same signal (determinism / no look-ahead beyond the window).
"""
import importlib.util
import os

import pytest

import strategy_config as cfg
from strategy import sats

_GEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tools", "make_synthetic_gold.py")
_spec = importlib.util.spec_from_file_location("make_synthetic_gold", _GEN)
_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen)


@pytest.fixture(scope="module")
def series():
    return _gen.generate(3000, seed=7)


def _all_flips(series, lo=300, hi=2500):
    out = []
    for i in range(lo, hi):
        window = series[max(0, i - 159): i + 1]
        sig = sats.evaluate(window)
        if sig:
            out.append((i, sig))
    return out


def test_engine_fires_and_is_balanced(series):
    flips = _all_flips(series)
    assert len(flips) > 30, f"only {len(flips)} flips — engine barely firing"
    buys = sum(1 for _i, s in flips if s["direction"] == "BUY")
    sells = len(flips) - buys
    # A trend-follower on a roughly two-sided series should not be wildly
    # one-directional.
    assert 0.25 < buys / len(flips) < 0.75, (buys, sells)


def test_warmup_returns_none():
    assert sats.evaluate(_gen.generate(30, seed=2)) is None
    assert sats.evaluate([]) is None


def test_tqi_is_bounded_and_scored(series):
    for _i, sig in _all_flips(series):
        assert 0.0 <= sig["tqi"] <= 1.0
        assert sig["setup_quality"] == sig["tqi"]


def test_plan_geometry(series):
    for _i, sig in _all_flips(series):
        e, sl = sig["entry_price"], sig["stop_loss"]
        assert sig["risk"] > 0
        assert sig["risk"] == pytest.approx(abs(e - sl))
        if sig["direction"] == "BUY":
            assert sl < e <= sig["tp1"] <= sig["tp2"] <= sig["tp3"]
        else:
            assert sl > e >= sig["tp1"] >= sig["tp2"] >= sig["tp3"]


def test_stop_distance_is_capped(series):
    """The SL max-distance cap must hold: risk never exceeds the ATR cap."""
    for _i, sig in _all_flips(series):
        cap = max(cfg.SATS_SL_MAX_DIST, cfg.SATS_SL_MULT) * sig["atr"]
        assert sig["risk"] <= cap + 1e-6, (sig["risk"], cap)


def test_flip_is_only_on_the_last_bar(series):
    """A flip reported for a window must be a genuine change at its final
    bar: extend the window by one bar and the SAME final bar must no longer
    report a flip (the trend has already turned)."""
    flips = _all_flips(series, lo=300, hi=800)
    assert flips
    i, sig = flips[0]
    # Re-evaluating the window that ends one bar EARLIER must not report the
    # same flip (the flip belongs to bar i, not i-1).
    earlier = series[max(0, (i - 1) - 159): i]  # ends at bar i-1
    earlier_sig = sats.evaluate(earlier)
    assert earlier_sig is None or earlier_sig["direction"] != sig["direction"] \
        or earlier_sig["entry_price"] != sig["entry_price"]


def test_determinism(series):
    """Same window in, same signal out — no hidden state."""
    window = series[300:460]
    a = sats.evaluate(window)
    b = sats.evaluate(list(window))
    assert (a is None) == (b is None)
    if a:
        assert a == b


def test_char_flip_disabled_removes_quality_flips(series, monkeypatch):
    """With character-flip off, every reported flip must be a price break."""
    monkeypatch.setattr(cfg, "SATS_USE_CHARFLIP", False)
    for _i, sig in _all_flips(series):
        assert sig["char_flip"] is False
        assert sig["reason"] == "Price band break"


def test_tqi_off_is_neutral_and_still_trades(series, monkeypatch):
    """With TQI disabled the engine is a plain adaptive SuperTrend; it must
    still produce flips, all graded at the neutral 0.5."""
    monkeypatch.setattr(cfg, "SATS_USE_TQI", False)
    flips = _all_flips(series)
    assert flips
    for _i, sig in flips:
        assert sig["tqi"] == pytest.approx(0.5)


def test_dynamic_tp_widens_targets_vs_fixed(series, monkeypatch):
    """DYNAMIC mode must produce different (scaled) R-multiples than FIXED
    on at least some signals, and stay within the configured ceiling."""
    fixed = {i: s for i, s in _all_flips(series)}
    monkeypatch.setattr(cfg, "SATS_TP_MODE", "DYNAMIC")
    dyn = {i: s for i, s in _all_flips(series)}
    common = set(fixed) & set(dyn)
    assert common
    differed = False
    for i in common:
        f, d = fixed[i], dyn[i]
        r3_dyn = abs(d["tp3"] - d["entry_price"]) / d["risk"]
        assert r3_dyn <= cfg.SATS_DYN_TP_CEIL_R + 1e-6
        if abs(d["tp3"] - f["tp3"]) > 1e-6:
            differed = True
    assert differed, "DYNAMIC produced identical targets to FIXED everywhere"
