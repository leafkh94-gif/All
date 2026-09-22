"""Tests for ENTRY_MODE: the REVERSION vs MOMENTUM head-to-head.

These guard three things that are easy to break silently:

  1. The two modes are genuinely different populations -- a momentum BUY
     must key off the n-bar HIGH, a reversion BUY off the n-bar LOW. An
     earlier version of the proximity gate was expressed in ATR and
     could not tell them apart at all (see test_band_quality_binds).
  2. Everything OTHER than the entry evidence is identical between
     modes, because that is what makes the backtest a fair comparison.
  3. The reversion mode's two evidence axes are anti-correlated, which is
     a structural property worth pinning: if someone "fixes" it by
     reweighting, these numbers should move and the test should be
     re-read rather than silently passing.
"""
import importlib.util
import os
import statistics as st

import pandas as pd
import pytest

import strategy_config as cfg
import scoring_strategy as strat
from strategy import golden_trio as gt

_GEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tools", "make_synthetic_gold.py")
_spec = importlib.util.spec_from_file_location("make_synthetic_gold", _GEN)
_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen)


@pytest.fixture(scope="module")
def candles():
    """A realistic series long enough that both modes fire many times."""
    return _gen.generate(3000, seed=11)


@pytest.fixture(scope="module")
def buys(candles):
    """Both modes' BUY populations, scanned once.

    The scan is the expensive part of this file (every bar re-runs the
    detector), so it is computed once per module rather than per test."""
    return {m: _buys(candles, m) for m in ("REVERSION", "MOMENTUM")}


def _buys(candles, mode, lo=400, hi=1300):
    """Collect BUY candidates with their position in the Donchian channel."""
    out = []
    for i in range(lo, hi):
        c = gt.find_golden_trio_candidate(candles[:i], entry_mode=mode)
        if not c or c["direction"] != "BUY":
            continue
        band_lo, band_hi = c["turtle_lower"], c["turtle_upper"]
        if band_hi <= band_lo:
            continue
        out.append({
            "pos": (c["entry_price"] - band_lo) / (band_hi - band_lo),
            "rsi_q": c["rsi_quality"],
            "turtle_q": c["turtle_quality"],
            "quality": c["setup_quality"],
        })
    return out


# ─────────────────────────────────────────────────────────────────────
# Mode plumbing
# ─────────────────────────────────────────────────────────────────────
def test_both_modes_fire_and_tag_themselves(candles):
    for mode in ("REVERSION", "MOMENTUM"):
        c = None
        for i in range(400, 900):
            c = gt.find_golden_trio_candidate(candles[:i], entry_mode=mode)
            if c:
                break
        assert c, f"{mode} never fired in 500 bars"
        assert c["entry_mode"] == mode


def test_unknown_entry_mode_is_refused(candles):
    cand, reason = gt.find_golden_trio_candidate_diag(
        candles[:600], entry_mode="SIDEWAYS")
    assert cand is None
    assert "ENTRY_MODE" in reason


def test_find_candidate_returns_sats_and_ignores_entry_mode(candles):
    """The live path now runs SATS. scoring_strategy.find_candidate returns
    SATS candidates and accepts entry_mode only for signature compatibility
    (SATS has no reversion/momentum variants). The Golden Trio modes are
    still exercised directly against strategy.golden_trio above."""
    saw_sats = False
    for i in range(400, 900):
        a = strat.find_candidate(candles[:i], entry_mode="REVERSION")
        b = strat.find_candidate(candles[:i], entry_mode="MOMENTUM")
        # entry_mode must not change what find_candidate returns.
        assert (a is None) == (b is None)
        if a is not None:
            assert a["pattern"] == "SATS"
            assert b["pattern"] == "SATS"
            assert a["entry_price"] == b["entry_price"]
            saw_sats = True
    assert saw_sats, "SATS never fired on the fixture series"


def test_default_mode_is_reversion_and_matches_config(candles):
    """Passing nothing must equal passing the configured mode."""
    for i in range(400, 700):
        a = gt.find_golden_trio_candidate(candles[:i])
        b = gt.find_golden_trio_candidate(candles[:i], entry_mode=cfg.ENTRY_MODE)
        assert (a is None) == (b is None)
        if a:
            assert a["entry_price"] == pytest.approx(b["entry_price"])


# ─────────────────────────────────────────────────────────────────────
# The modes must be genuinely different
# ─────────────────────────────────────────────────────────────────────
def test_momentum_buys_sit_higher_in_the_channel_than_reversion_buys(buys):
    rev, mom = buys["REVERSION"], buys["MOMENTUM"]
    assert len(rev) > 50 and len(mom) > 50, (len(rev), len(mom))
    # Compare on the population the score actually selects: the best half.
    rev_top = sorted(rev, key=lambda r: -r["quality"])[:len(rev) // 2]
    mom_top = sorted(mom, key=lambda r: -r["quality"])[:len(mom) // 2]
    rev_pos = st.median(r["pos"] for r in rev_top)
    mom_pos = st.median(r["pos"] for r in mom_top)
    assert mom_pos > rev_pos, (
        f"momentum BUYs (median {mom_pos:.2f}) must sit higher in the "
        f"channel than reversion BUYs (median {rev_pos:.2f})")


def test_turtle_evidence_points_opposite_ways_in_the_two_modes(buys):
    """turtle_quality must reward a LOW position in reversion and a HIGH
    position in momentum. This is the sign of the premise itself."""
    def corr(a, b):
        ma, mb = st.mean(a), st.mean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        den = (sum((x - ma) ** 2 for x in a) ** .5
               * sum((y - mb) ** 2 for y in b) ** .5)
        return num / den if den else 0.0

    rev, mom = buys["REVERSION"], buys["MOMENTUM"]
    rev_c = corr([r["turtle_q"] for r in rev], [r["pos"] for r in rev])
    mom_c = corr([r["turtle_q"] for r in mom], [r["pos"] for r in mom])
    assert rev_c < -0.3, f"reversion turtle_quality vs position = {rev_c:+.2f}"
    assert mom_c > +0.3, f"momentum turtle_quality vs position = {mom_c:+.2f}"


def test_reversion_evidence_axes_are_anti_correlated(buys):
    """Documented structural defect, pinned so it cannot be lost.

    In REVERSION, rsi_quality rewards price having ALREADY climbed while
    turtle_quality rewards price being at the LOW. Summing two
    anti-correlated signals at 0.6/0.4 compresses setup_quality toward
    its mean, which is one reason the score never ordered outcomes.
    MOMENTUM does not have this problem: both axes agree."""
    def corr(a, b):
        ma, mb = st.mean(a), st.mean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        den = (sum((x - ma) ** 2 for x in a) ** .5
               * sum((y - mb) ** 2 for y in b) ** .5)
        return num / den if den else 0.0

    rev, mom = buys["REVERSION"], buys["MOMENTUM"]
    rev_c = corr([r["rsi_q"] for r in rev], [r["turtle_q"] for r in rev])
    mom_c = corr([r["rsi_q"] for r in mom], [r["turtle_q"] for r in mom])
    assert rev_c < 0, f"expected reversion axes to conflict, got {rev_c:+.2f}"
    assert mom_c > 0, f"expected momentum axes to agree, got {mom_c:+.2f}"
    assert mom_c > rev_c


# ─────────────────────────────────────────────────────────────────────
# The proximity gate must actually bind
# ─────────────────────────────────────────────────────────────────────
def test_band_quality_binds_on_channel_width_not_atr():
    """The regression this replaces: thresholds in ATR could not reject.

    A 10-bar Donchian measures ~3 ATR wide, so a 5-ATR hard cap was 1.7x
    the whole channel and rejected nothing inside it. Expressed as a
    fraction of channel width, a bar most of the way across is refused."""
    width = 30.0
    atr = 10.0   # channel is only 3 ATR wide, as in real gold
    ok_on_band, q_on_band = gt._band_quality(0.0, width, atr)
    assert ok_on_band and q_on_band == pytest.approx(1.0)

    # Just inside the hard fraction -> still fires, but weak.
    inside = (cfg.GT_PROXIMITY_CHANNEL_HARD - 0.05) * width
    ok_in, q_in = gt._band_quality(inside, width, atr)
    assert ok_in and 0.0 <= q_in < 0.2

    # Beyond it -> refused. Under the old ATR form this distance (~0.75
    # ATR-normalised) was nowhere near the 5-ATR cap and passed.
    beyond = (cfg.GT_PROXIMITY_CHANNEL_HARD + 0.05) * width
    ok_out, q_out = gt._band_quality(beyond, width, atr)
    assert not ok_out and q_out == 0.0
    assert beyond < cfg.GT_PROXIMITY_ATR_HARD_VETO * atr, (
        "this distance must be one the OLD ATR cap would have accepted, "
        "or the test is not pinning the regression")


def test_band_quality_falls_back_when_channel_collapses():
    """A flat patch has ~zero width; without a fallback the fraction
    divides by ~0 and accepts everything."""
    ok, q = gt._band_quality(0.0, 0.0, 10.0)
    assert ok and q == pytest.approx(1.0)
    ok_far, _ = gt._band_quality(999.0, 0.0, 10.0)
    assert not ok_far


def test_pierced_band_scores_full_quality():
    """Distance <= 0 means the band is reached or broken."""
    for d in (0.0, -5.0, -50.0):
        ok, q = gt._band_quality(d, 30.0, 10.0)
        assert ok and q == pytest.approx(1.0), d


# ─────────────────────────────────────────────────────────────────────
# Everything else must be identical
# ─────────────────────────────────────────────────────────────────────
def test_structural_targets_are_refused_in_momentum_mode(candles, monkeypatch):
    """STRUCTURAL puts the stop past `band`, which in momentum mode is
    ABOVE a BUY entry. Emitting that would be an inverted stop."""
    monkeypatch.setattr(cfg, "TARGET_MODE", "STRUCTURAL")
    reasons = []
    for i in range(400, 800):
        cand, reason = gt.find_golden_trio_candidate_diag(
            candles[:i], entry_mode="MOMENTUM")
        assert cand is None, "momentum must not emit STRUCTURAL targets"
        if reason:
            reasons.append(reason)
    assert any("structural-targets-need-REVERSION" in r for r in reasons)


def test_stops_are_on_the_correct_side_in_both_modes(candles):
    """Whatever the premise, a BUY stop is below entry and a SELL stop above."""
    checked = 0
    for mode in ("REVERSION", "MOMENTUM"):
        for i in range(400, 800):
            c = gt.find_golden_trio_candidate(candles[:i], entry_mode=mode)
            if not c:
                continue
            checked += 1
            if c["direction"] == "BUY":
                assert c["stop_loss"] < c["entry_price"] < c["tp1"] <= c["tp2"] <= c["tp3"]
            else:
                assert c["stop_loss"] > c["entry_price"] > c["tp1"] >= c["tp2"] >= c["tp3"]
    assert checked > 100, checked


def test_risk_is_positive_and_matches_stop_distance(candles):
    for mode in ("REVERSION", "MOMENTUM"):
        for i in range(400, 900):
            c = gt.find_golden_trio_candidate(candles[:i], entry_mode=mode)
            if not c:
                continue
            assert c["risk"] > 0
            assert c["risk"] == pytest.approx(abs(c["entry_price"] - c["stop_loss"]))


def test_setup_quality_stays_in_unit_range(candles):
    for mode in ("REVERSION", "MOMENTUM"):
        for i in range(400, 700):
            c = gt.find_golden_trio_candidate(candles[:i], entry_mode=mode)
            if c:
                assert 0.0 <= c["setup_quality"] <= 1.0
