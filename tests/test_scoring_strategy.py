from unittest.mock import patch

import scoring_strategy as strat
from strategy import modes
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


def test_vwap_filter_score_neutral_without_volume_data():
    """make_candles always sets v=None, so anchored_vwap can't be computed --
    must fail to neutral, not silently favor either direction."""
    df = strat._df(make_candles(30, start_price=100.0, noise=0.3))
    assert strat.vwap_filter_score(df, "BUY") == strat.cfg.VWAP_FILTER_NEUTRAL


def test_vwap_filter_score_match_when_price_above_vwap():
    import datetime as dt
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    candles = [
        {"t": "2026-07-01T09:00:00", "o": 100, "h": 100, "l": 100, "c": 100, "v": 10},
        {"t": "2026-07-01T10:00:00", "o": 120, "h": 120, "l": 120, "c": 120, "v": 10},
    ]
    df = strat._df(candles)
    assert strat.vwap_filter_score(df, "BUY", now_utc=now) == strat.cfg.VWAP_FILTER_MATCH
    assert strat.vwap_filter_score(df, "SELL", now_utc=now) == strat.cfg.VWAP_FILTER_AGAINST


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
         patch.object(strat, "vwap_filter_score", return_value=4):
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
         patch.object(strat, "vwap_filter_score", return_value=0), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(0, "NONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)), \
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
                 "sweep_price": 100.0, "leg_extreme": 95.0, "quality": 38}
    import datetime as dt
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "vwap_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store(), diagnostic=True)
    assert result["blocked"] is None
    assert result["score"] >= 75
    assert result["pattern"] == "LIQUIDITY_SWEEP_BOS"


def test_score_candidate_degenerate_leg_is_skipped_not_degraded():
    """A qualifying score whose leg_extreme sits on the wrong side of the
    breakout close (an invalid/degenerate leg) must be skipped entirely, not
    sent with a nonsensical entry."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "leg_extreme": 999.0, "quality": 38}
    import datetime as dt
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "vwap_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store())
        diag = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store(), diagnostic=True)
    assert result is None
    assert diag["blocked"] == "degenerate leg (entry construction failed)"


def _level_store_with_near_pdh(high):
    class _Store:
        def get_daily_levels(self, instrument):
            return {"high": high, "low": 50.0}

        def get_weekly_levels(self, instrument):
            return None
    return _Store()


def test_score_candidate_skipped_when_rr_falls_below_min_after_liquidity_cap():
    """A raw TP2 that would clear the R:R minimum, but sits well past a nearby
    PDH, must be capped at the PDH -- and if that capped R:R then falls below
    MIN_RR_AFTER_CAP, the alert is skipped outright (not sent with a degraded
    target)."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "leg_extreme": 95.0, "quality": 38}
    import datetime as dt
    # PDH sits just above the expected ~97.5 entry -- close enough that capping
    # TP2 there collapses the R:R well under the 1.5 floor.
    level_store = _level_store_with_near_pdh(98.5)
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "vwap_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc), level_store)
        diag = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc), level_store, diagnostic=True)
    assert result is None
    assert diag["blocked"] == "RR_BELOW_MIN_AFTER_LIQUIDITY_CAP"


def _level_store_with_pdh_and_pwh(pdh, pwh):
    class _Store:
        def get_daily_levels(self, instrument):
            return {"high": pdh, "low": 50.0}

        def get_weekly_levels(self, instrument):
            return {"high": pwh, "low": 40.0}
    return _Store()


def test_score_candidate_caps_tp1_and_walks_tp2_to_next_level_beyond_it():
    """TP1 now gets liquidity awareness too (trader-review fix), and TP2 must
    target the NEXT level beyond wherever TP1 landed rather than the same
    nearest level -- otherwise both would cap to an identical, redundant
    price."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "leg_extreme": 95.0, "quality": 38}
    import datetime as dt
    # ~97.5 entry, ~104.0 raw TP1, ~107.25 raw TP2 (see the RR-after-cap test
    # above for the same setup's math). pdh sits between entry+1R and raw
    # TP1 -- close enough to cap TP1 but still clear MIN_RR_TP1_AFTER_CAP;
    # pwh sits beyond that capped TP1 and well under raw TP2.
    level_store = _level_store_with_pdh_and_pwh(pdh=101.5, pwh=105.0)
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "vwap_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc), level_store)
    assert result is not None
    assert result["tp1_capped"] is True
    assert result["tp1"] == 101.5
    assert result["tp2_capped"] is True
    assert result["tp2"] == 105.0
    assert result["tp1"] < result["tp2"]


def test_diagnostic_mode_qualifying_result_survives_main_alerts_diagnostics_dict():
    """Regression test: main_alerts.run() builds a diagnostics dict via
    scored["pattern"]/["direction"]/["score"]/["blocked"] for every candidate,
    qualifying or not. A prior bug omitted "pattern" from the qualifying-case
    result (it was only nested under result["breakdown"]["pattern"]), which
    raised a KeyError and silently killed every scan that found a real signal,
    blocking all alerts. This reproduces that exact access pattern."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "leg_extreme": 95.0, "quality": 38}
    import datetime as dt
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "vwap_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        scored = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store(), diagnostic=True)
    diagnostic_entry = {"pattern": scored["pattern"], "direction": scored["direction"],
                         "score": scored["score"], "blocked": scored["blocked"]}
    assert diagnostic_entry["pattern"] == "LIQUIDITY_SWEEP_BOS"


def _ranging_market(quality):
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": make_candles(260, start_price=100.0, step=0.0, noise=0.5, interval_minutes=240),
    }
    candidate = {"pattern": "FLAG", "direction": "BUY", "sweep_price": 100.0,
                 "leg_extreme": 95.0, "quality": quality}
    return market, candidate


def test_score_candidate_loose_mode_lower_watch_threshold():
    """A setup scoring ~58 (RANGING bias +5, quality 27) must be blocked under
    the default 62 threshold but pass under loose mode's 55 threshold."""
    import datetime as dt
    market, candidate = _ranging_market(quality=27)
    now = dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc)
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "vwap_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.ind, "volume_profile_zones", return_value=(None, None, None)), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        default_result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store())
        loose_result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store(), mode=modes.LOOSE)
    assert default_result is None
    assert loose_result is not None
    assert loose_result["score"] == 58


def test_score_candidate_diagnostic_blocked_message_reflects_mode_threshold():
    import datetime as dt
    market, candidate = _ranging_market(quality=10)
    now = dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc)
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "vwap_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.ind, "volume_profile_zones", return_value=(None, None, None)), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        default_result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store(), diagnostic=True)
        loose_result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store(),
            diagnostic=True, mode=modes.LOOSE)
    assert "62" in default_result["blocked"]
    assert "55" in loose_result["blocked"]


def test_with_trend_signal_is_not_blocked_and_scores():
    """A BUY signal in a confirmed uptrend must not be hard-blocked, and with all
    sub-factors forced to known values it must clear the A+ threshold."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "leg_extreme": 95.0, "quality": 38}
    import datetime as dt
    # Pin every upgrade-module factor that isn't the subject of this test (ATR
    # regime / choppiness / FVG / EQH-EQL) so the assertion isolates the
    # counter-trend gate rather than depending on synthetic-data noise.
    with patch.object(strat, "technical_confirm_score", return_value=10), \
         patch.object(strat, "vwap_filter_score", return_value=4), \
         patch.object(strat, "choppy_market_penalty", return_value=0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "fvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)), \
         patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]):
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store())
    assert result is not None
    assert result["direction"] == "BUY"
    assert result["score"] >= 75


def test_compute_entry_exit_buy_invariants():
    candidate = {"direction": "BUY", "leg_extreme": 99.0}
    breakout = {"o": 100.0, "h": 103.0, "l": 99.5, "c": 102.0}
    exits = strat.compute_entry_exit(candidate, breakout, atr_value=1.0, retrace_pct=0.5, rng=_zero_rng())
    assert exits["stop_loss"] < exits["entry_price"] < exits["tp1"] < exits["tp2"]


def test_compute_entry_exit_sell_invariants():
    candidate = {"direction": "SELL", "leg_extreme": 101.0}
    breakout = {"o": 100.0, "h": 100.5, "l": 97.0, "c": 98.0}
    exits = strat.compute_entry_exit(candidate, breakout, atr_value=1.0, retrace_pct=0.5, rng=_zero_rng())
    assert exits["tp2"] < exits["tp1"] < exits["entry_price"] < exits["stop_loss"]


def test_compute_entry_exit_degenerate_leg_returns_none():
    """BUY leg requires close > leg_extreme -- if the breakout candle never
    cleared the leg extreme, there's no valid leg to retrace, skip the setup."""
    candidate = {"direction": "BUY", "leg_extreme": 100.0}
    breakout = {"o": 100.0, "h": 100.5, "l": 99.5, "c": 99.8}
    exits = strat.compute_entry_exit(candidate, breakout, atr_value=1.0, retrace_pct=0.5, rng=_zero_rng())
    assert exits is None


def test_compute_entry_exit_fvg_inside_leg_overrides_retrace_entry():
    candidate = {"direction": "BUY", "leg_extreme": 90.0}
    breakout = {"o": 100.0, "h": 103.0, "l": 99.5, "c": 100.0}
    fvg_zones = [{"direction": "BULLISH", "bottom": 94.0, "top": 96.0, "index": 0}]
    exits = strat.compute_entry_exit(
        candidate, breakout, atr_value=1.0, retrace_pct=0.5, fvg_zones=fvg_zones, rng=_zero_rng())
    assert exits is not None
    assert exits["entry_price"] == 96.0
    assert exits["entry_basis"] == "FVG edge"


def test_compute_entry_exit_ignores_fvg_zone_below_min_size():
    """A gap smaller than MIN_FVG_SIZE_ATR_MULT x ATR must not override the
    retrace entry -- a negligible gap isn't meaningfully different from noise."""
    candidate = {"direction": "BUY", "leg_extreme": 90.0}
    breakout = {"o": 100.0, "h": 103.0, "l": 99.5, "c": 100.0}
    fvg_zones = [{"direction": "BULLISH", "bottom": 94.99, "top": 95.0, "index": 0}]  # 0.01-wide, atr=1.0
    exits = strat.compute_entry_exit(
        candidate, breakout, atr_value=1.0, retrace_pct=0.5, fvg_zones=fvg_zones, rng=_zero_rng())
    assert exits is not None
    assert exits["entry_basis"] == "50% leg retrace"  # FVG override never applied


def test_compute_entry_exit_leg_size_floored_for_thin_same_candle_leg():
    """A thin same-candle leg (raw size 0.3) must still produce a realistic
    pullback distance once floored to MIN_LEG_ATR_MULT x ATR, not an entry
    that barely differs from the breakout candle's own close."""
    candidate = {"direction": "BUY", "leg_extreme": 99.7}
    breakout = {"o": 100.0, "h": 100.1, "l": 99.6, "c": 100.0}
    exits = strat.compute_entry_exit(candidate, breakout, atr_value=1.0, retrace_pct=0.2, rng=_zero_rng())
    assert exits is not None
    assert exits["entry_price"] == 99.8  # floored leg_size (1.0) x 0.2, not raw (0.3) x 0.2 = 99.94


def test_compute_entry_exit_stop_buffer_scales_with_leg_size():
    """A large leg must widen the stop buffer beyond the flat ATR floor,
    proportional to the size of the actual move, not a fixed thin pad."""
    candidate = {"direction": "BUY", "leg_extreme": 90.0}
    breakout = {"o": 100.0, "h": 100.5, "l": 99.5, "c": 100.0}
    exits = strat.compute_entry_exit(candidate, breakout, atr_value=1.0, retrace_pct=0.5, rng=_zero_rng())
    assert exits is not None
    # leg_size=10 (well above the 1x ATR floor); buffer = max(0.35*1, 0.15*10=1.5) = 1.5
    assert exits["stop_loss"] == 88.5


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


def _df_with_spike(spike_bars_ago, n=10, atr_value=1.0):
    """n normal small-range bars, with one bar spike_bars_ago (0 = most recent
    completed bar before 'current') widened to a large range."""
    candles = make_candles(n, start_price=100.0, noise=0.05)
    idx = n - 1 - spike_bars_ago
    candles[idx]["h"] = candles[idx]["o"] + 10 * atr_value
    candles[idx]["l"] = candles[idx]["o"] - 10 * atr_value
    return strat._df(candles)


def test_recent_spike_penalty_applies_to_non_news_pattern_after_spike():
    df = _df_with_spike(spike_bars_ago=1)  # within the 3-bar lookback, excluding current
    penalty = strat.recent_spike_penalty(df, atr_value=1.0, candidate_pattern="LIQUIDITY_SWEEP_BOS")
    assert penalty == strat.cfg.RECENT_SPIKE_PENALTY


def test_recent_spike_penalty_exempts_news_retest_pattern():
    df = _df_with_spike(spike_bars_ago=1)
    penalty = strat.recent_spike_penalty(df, atr_value=1.0, candidate_pattern="NEWS_RETEST")
    assert penalty == 0


def test_recent_spike_penalty_no_penalty_without_recent_spike():
    df = strat._df(make_candles(10, start_price=100.0, noise=0.05))
    penalty = strat.recent_spike_penalty(df, atr_value=1.0, candidate_pattern="FLAG")
    assert penalty == 0


def test_recent_spike_penalty_ignores_spike_outside_lookback_window():
    df = _df_with_spike(spike_bars_ago=5)  # older than RECENT_SPIKE_LOOKBACK=3
    penalty = strat.recent_spike_penalty(df, atr_value=1.0, candidate_pattern="SD_REJECTION")
    assert penalty == 0
