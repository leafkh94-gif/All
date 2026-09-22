"""Tests for the scoring engine (SATS).

The bot runs SATS as its sole strategy: a confirmed SuperTrend flip is the
signal, graded by TQI, with score = round(100 * TQI). These tests cover the
scoring_strategy wrapper — the SATS engine itself is covered in test_sats.py.
"""
import datetime as dt
import importlib.util
import os

import pytest

import scoring_strategy as strat
import strategy_config as cfg
from strategy import modes

_GEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tools", "make_synthetic_gold.py")
_spec = importlib.util.spec_from_file_location("make_synthetic_gold", _GEN)
_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen)


def _now():
    return dt.datetime(2026, 7, 1, 7, 30, tzinfo=dt.timezone.utc)


def _market(entry_candles):
    # SATS is single-timeframe; the extra frames are accepted but ignored.
    return {"entry": entry_candles, "m15": entry_candles, "h1": [], "h4": []}


def _first_flip(seed=7, lo=300, hi=2500):
    """Return (candidate, window) for the first SATS flip on a seeded series."""
    candles = _gen.generate(hi + 5, seed=seed)
    for i in range(lo, hi):
        window = candles[max(0, i - 159): i + 1]
        c = strat.find_candidate(window)
        if c:
            return c, window
    raise AssertionError("SATS never fired on the fixture series")


# ─── find_candidate ──────────────────────────────────────────────────
def test_find_candidate_returns_a_sats_signal():
    cand, _window = _first_flip()
    assert cand["pattern"] == "SATS"
    assert cand["direction"] in ("BUY", "SELL")
    for key in ("entry_price", "stop_loss", "tp1", "tp2", "tp3", "risk", "tqi"):
        assert key in cand, f"SATS candidate missing {key}"


def test_find_candidate_returns_none_on_too_few_bars():
    assert strat.find_candidate(_gen.generate(30, seed=1)) is None


def test_stop_and_targets_are_on_the_correct_side():
    cand, _window = _first_flip()
    e, sl = cand["entry_price"], cand["stop_loss"]
    if cand["direction"] == "BUY":
        assert sl < e < cand["tp1"] <= cand["tp2"] <= cand["tp3"]
    else:
        assert sl > e > cand["tp1"] >= cand["tp2"] >= cand["tp3"]
    assert cand["risk"] == pytest.approx(abs(e - sl))


def test_entry_and_target_modes_are_ignored_but_accepted():
    """The backtester passes target_mode/entry_mode; SATS ignores them but
    the signature must accept them without changing the result."""
    _cand, window = _first_flip()
    a = strat.find_candidate(window)
    b = strat.find_candidate(window, target_mode="FIXED", entry_mode="MOMENTUM")
    assert a["entry_price"] == b["entry_price"]
    assert a["direction"] == b["direction"]


# ─── score_candidate ─────────────────────────────────────────────────
def test_score_is_tqi_times_one_hundred():
    cand, window = _first_flip()
    scored = strat.score_candidate("XAUUSD", "COMMODITY", cand, _market(window),
                                   _now(), None)
    assert scored["score"] == round(100 * cand["tqi"])
    assert 0 <= scored["score"] <= 100


def test_tier_thresholds_follow_config():
    cand, window = _first_flip()
    scored = strat.score_candidate("XAUUSD", "COMMODITY", cand, _market(window),
                                   _now(), None)
    s = scored["score"]
    if s >= cfg.APLUS_MIN_SCORE:
        assert scored["tier"] == "A+" and scored["aplus_eligible"]
    elif s >= cfg.WATCH_MIN_SCORE:
        assert scored["tier"] == "WATCH" and not scored["aplus_eligible"]
    else:
        assert scored["tier"] == "NONE"


def test_scored_dict_carries_every_key_the_formatter_and_backtest_read():
    cand, window = _first_flip()
    scored = strat.score_candidate("XAUUSD", "COMMODITY", cand, _market(window),
                                   _now(), None)
    # Keys read with bracket access downstream (alert formatter + backtest).
    for key in ("instrument", "direction", "pattern", "score", "tier",
                "htf_bias", "zlsma_status", "entry_price", "stop_loss",
                "tp1", "tp2", "tp3", "risk", "breakdown", "setup_quality",
                "mtf_points", "mtf_available", "aplus_eligible", "target_mode"):
        assert key in scored, f"scored dict missing {key}"


def test_breakdown_values_are_ints_for_the_formatter():
    """main_alerts._breakdown_summary renders pts with ':+d' — any non-int
    breakdown value crashes the alert. Guard it."""
    cand, window = _first_flip()
    scored = strat.score_candidate("XAUUSD", "COMMODITY", cand, _market(window),
                                   _now(), None)
    for tag, pts in scored["breakdown"]:
        assert isinstance(pts, int), f"breakdown {tag} is {type(pts).__name__}, not int"


def test_score_candidate_none_returns_none():
    assert strat.score_candidate("XAUUSD", "COMMODITY", None, {}, _now(), None) is None


def test_mode_thresholds_override_config():
    """A mode's watch/aplus thresholds must win over the config defaults."""
    cand, window = _first_flip()
    strict = modes.STANDARD._replace(watch_min_score=95, aplus_min_score=99)
    scored = strat.score_candidate("XAUUSD", "COMMODITY", cand, _market(window),
                                   _now(), None, mode=strict)
    # With a 95 WATCH floor almost every real flip grades NONE.
    if scored["score"] < 95:
        assert scored["tier"] == "NONE"


# ─── A+ confirmation helper ──────────────────────────────────────────
def test_confirmation_closed_in_direction():
    assert strat.confirmation_closed_in_direction({"o": 1.0, "c": 1.5}, "BUY")
    assert not strat.confirmation_closed_in_direction({"o": 1.0, "c": 0.5}, "BUY")
    assert strat.confirmation_closed_in_direction({"o": 1.0, "c": 0.5}, "SELL")
    assert not strat.confirmation_closed_in_direction(None, "BUY")


def test_pending_aplus_store_roundtrip(tmp_path):
    store = strat.PendingAPlusStore(path=str(tmp_path / "p.json"))
    store.add("XAUUSD", {"score": 72})
    assert store.get("XAUUSD") == {"score": 72}
    assert store.items() == [("XAUUSD", {"score": 72})]
    store.remove("XAUUSD")
    assert store.get("XAUUSD") is None
