from unittest.mock import patch

import scoring_strategy as strat
from tests.helpers import make_candles, trending_h4_candles


def test_htf_bias_trending_up():
    assert strat.htf_bias(trending_h4_candles(up=True)) == "TRENDING_UP"


def test_htf_bias_trending_down():
    assert strat.htf_bias(trending_h4_candles(up=False)) == "TRENDING_DOWN"


def test_htf_bias_ranging_when_flat():
    flat = make_candles(260, start_price=5000.0, step=0.0, noise=0.5, interval_minutes=240)
    assert strat.htf_bias(flat) == "RANGING"


def test_htf_bias_ranging_when_insufficient_data():
    assert strat.htf_bias(make_candles(10)) == "RANGING"


def test_daily_bias_score_with_trend():
    pts, tag = strat.daily_bias_score("TRENDING_UP", "BUY")
    assert (pts, tag) == (15, "with_trend")


def test_daily_bias_score_neutral():
    pts, tag = strat.daily_bias_score("RANGING", "SELL")
    assert (pts, tag) == (5, "neutral")


def _fake_level_store():
    class _Store:
        def get_daily_levels(self, instrument):
            return None

        def get_weekly_levels(self, instrument):
            return None
    return _Store()


def test_counter_trend_hard_block_sell_in_uptrend():
    """Section 2 — PRIORITY FIX: SELL signals must be hard-blocked in an uptrend."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "SELL",
                 "sweep_price": 100.0, "quality": 38}
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "ma20_filter_score", return_value=4):
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            __import__("datetime").datetime(2026, 1, 1, 12, 45, tzinfo=__import__("datetime").timezone.utc),
            _fake_level_store())
    assert result is None


def test_counter_trend_hard_block_buy_in_downtrend():
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=False),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    import datetime as dt
    result = strat.score_candidate(
        "US500", "US_INDEX", candidate, market, dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
        _fake_level_store())
    assert result is None


def test_diagnostic_mode_reports_counter_trend_block_instead_of_none():
    """/scan needs a reason string even when the setup is hard-blocked."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "SELL",
                 "sweep_price": 100.0, "quality": 38}
    import datetime as dt
    result = strat.score_candidate(
        "US500", "US_INDEX", candidate, market,
        dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
        _fake_level_store(), diagnostic=True)
    assert result is not None
    assert result["score"] is None
    assert "counter-trend" in result["blocked"]


def test_diagnostic_mode_reports_below_threshold_score():
    """A setup that scores below WATCH_MIN_SCORE must still surface its score."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": make_candles(260, start_price=100.0, step=0.0, noise=0.5, interval_minutes=240),
    }
    candidate = {"pattern": "FLAG", "direction": "BUY", "sweep_price": 100.0, "quality": 5}
    import datetime as dt
    with patch.object(strat, "technical_confirm_score", return_value=0), \
         patch.object(strat, "ma20_filter_score", return_value=0), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(0, "NONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store(), diagnostic=True)
    assert result is not None
    assert result["score"] is not None
    assert result["score"] < 62
    assert "below WATCH threshold" in result["blocked"]


def test_diagnostic_mode_qualifying_setup_has_no_blocked_reason():
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    import datetime as dt
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "ma20_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store(), diagnostic=True)
    assert result["blocked"] is None
    assert result["score"] >= 75


def test_with_trend_signal_is_not_blocked_and_scores():
    """A BUY signal in a confirmed uptrend must not be hard-blocked, and with all
    sub-factors forced to known values it must clear the A+ threshold."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    import datetime as dt
    # Pin every upgrade-module factor that isn't the subject of this test (ATR
    # regime / choppiness / FVG / EQH-EQL) so the assertion isolates the
    # counter-trend gate rather than depending on synthetic-data noise.
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "ma20_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store())
    assert result is not None
    assert result["direction"] == "BUY"
    assert result["score"] >= 75


def test_compute_entry_exit_buy_invariants():
    candidate = {"direction": "BUY", "sweep_price": 100.0}
    breakout = {"o": 100.0, "h": 103.0, "l": 99.5, "c": 102.0}
    exits = strat.compute_entry_exit(candidate, breakout, atr_value=1.0, rng=_zero_rng())
    assert exits["stop_loss"] < exits["entry_price"] < exits["tp1"] < exits["tp2"]


def test_compute_entry_exit_sell_invariants():
    candidate = {"direction": "SELL", "sweep_price": 100.0}
    breakout = {"o": 100.0, "h": 100.5, "l": 97.0, "c": 98.0}
    exits = strat.compute_entry_exit(candidate, breakout, atr_value=1.0, rng=_zero_rng())
    assert exits["tp2"] < exits["tp1"] < exits["entry_price"] < exits["stop_loss"]


def _zero_rng():
    class _Rng:
        @staticmethod
        def uniform(a, b):
            return 0.0
    return _Rng()


def test_confirmation_closed_in_direction():
    assert strat.confirmation_closed_in_direction({"o": 100, "c": 101}, "BUY") is True
    assert strat.confirmation_closed_in_direction({"o": 100, "c": 99}, "BUY") is False
    assert strat.confirmation_closed_in_direction({"o": 100, "c": 99}, "SELL") is True


def test_pending_aplus_store_roundtrip(tmp_path):
    store = strat.PendingAPlusStore(path=str(tmp_path / "pending.json"))
    store.add("US500", {"score": 80, "direction": "BUY"})
    assert store.get("US500")["score"] == 80
    store.remove("US500")
    assert store.get("US500") is None
