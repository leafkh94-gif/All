"""Tests for the gold-only scoring engine (Golden Trio wrapper + tiny scorer)."""
import datetime as dt

import scoring_strategy as strat
import strategy_config as cfg
from strategy import modes
from tests.helpers import make_candles, trending_h4_candles
from tests.test_golden_trio import _long_setup_candles, _short_setup_candles


class _StubLevelStore:
    def get_daily_levels(self, _):
        return None

    def get_weekly_levels(self, _):
        return None


def _market(entry_candles, h4=None):
    return {
        "entry": entry_candles,
        "m15": entry_candles,
        "h1": entry_candles,
        "h4": h4 or trending_h4_candles(n=260, up=True),
    }


def _now():
    # A London killzone time so the killzone bonus applies.
    return dt.datetime(2026, 7, 1, 7, 30, tzinfo=dt.timezone.utc)


# ─── htf_bias ────────────────────────────────────────────────────────

def test_htf_bias_bull_on_uptrend():
    assert strat.htf_bias(trending_h4_candles(n=260, up=True)) == "BULL"


def test_htf_bias_bear_on_downtrend():
    assert strat.htf_bias(trending_h4_candles(n=260, up=False)) == "BEAR"


def test_htf_bias_flat_on_short_history():
    assert strat.htf_bias(make_candles(10)) == "FLAT"


# ─── find_candidate ──────────────────────────────────────────────────

def test_find_candidate_returns_golden_trio_dict_when_gates_align():
    result = strat.find_candidate(_long_setup_candles())
    assert result is not None
    assert result["pattern"] == "GOLDEN_TRIO"
    assert result["direction"] == "BUY"


def test_find_candidate_returns_none_on_flat_market():
    assert strat.find_candidate(make_candles(200, start_price=2000.0, step=0.0, noise=0.05)) is None


# ─── score_candidate ─────────────────────────────────────────────────

def test_score_candidate_returns_none_when_htf_bias_opposes_direction():
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles(), h4=trending_h4_candles(n=260, up=False))  # bearish H4
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, _now(), _StubLevelStore())
    assert scored is None


def test_score_candidate_returns_dict_with_expected_fields_on_aligned_bias():
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles())
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, _now(), _StubLevelStore())
    assert scored is not None
    for key in ("instrument", "direction", "pattern", "entry_price", "stop_loss",
                "tp1", "tp2", "tp3", "risk", "score", "breakdown", "htf_bias"):
        assert key in scored
    assert scored["htf_bias"] == "BULL"
    assert scored["direction"] == "BUY"


def test_score_candidate_score_is_bounded_0_100():
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles())
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, _now(), _StubLevelStore())
    assert 0 <= scored["score"] <= 100


def test_score_candidate_includes_killzone_bonus_in_breakdown_during_london():
    candidate = strat.find_candidate(_long_setup_candles())
    market = _market(_long_setup_candles())
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, _now(), _StubLevelStore())
    tags = [tag for tag, _ in scored["breakdown"]]
    assert "LONDON_KILLZONE" in tags


def test_score_candidate_short_setup_bias_bear_returns_scored_dict():
    candidate = strat.find_candidate(_short_setup_candles())
    market = _market(_short_setup_candles(), h4=trending_h4_candles(n=260, up=False))
    scored = strat.score_candidate("XAUUSD", "COMMODITY", candidate, market, _now(), _StubLevelStore())
    assert scored is not None
    assert scored["direction"] == "SELL"


# ─── PendingAPlusStore ───────────────────────────────────────────────

def test_pending_aplus_store_roundtrip(tmp_path):
    store = strat.PendingAPlusStore(path=str(tmp_path / "pending.json"))
    store.add("XAUUSD", {"score": 80, "direction": "BUY"})
    assert store.get("XAUUSD")["score"] == 80
    store.remove("XAUUSD")
    assert store.get("XAUUSD") is None


def test_pending_aplus_store_items_returns_all_entries(tmp_path):
    store = strat.PendingAPlusStore(path=str(tmp_path / "pending.json"))
    store.add("XAUUSD", {"score": 80})
    items = store.items()
    assert items == [("XAUUSD", {"score": 80})]


# ─── confirmation_closed_in_direction ────────────────────────────────

def test_confirmation_closed_in_direction():
    assert strat.confirmation_closed_in_direction({"o": 100, "c": 101}, "BUY") is True
    assert strat.confirmation_closed_in_direction({"o": 100, "c": 99}, "BUY") is False
    assert strat.confirmation_closed_in_direction({"o": 100, "c": 99}, "SELL") is True
    assert strat.confirmation_closed_in_direction(None, "BUY") is False
