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


_FAKE_LEG = {"leg_origin": 95.0, "leg_end": 100.5, "bos_index": 79}


_UNSET = object()


def _patch_qualifying_stack(stack, find_leg_return=_UNSET):
    """Common patch set for score_candidate tests that need a deterministic,
    qualifying result: pins every scoring sub-factor to a known value and
    stubs find_leg (BOS discovery has its own dedicated tests against real
    candle geometry -- these tests are about score_candidate's own control
    flow, not about find_leg's correctness) and the session-level helpers
    (so TP2/TP3 pool composition doesn't depend on synthetic-candle noise)."""
    stack.enter_context(patch.object(strat, "technical_confirm_score", return_value=10))
    stack.enter_context(patch.object(strat, "vwap_filter_score", return_value=4))
    stack.enter_context(patch.object(strat, "choppy_market_penalty", return_value=0))
    stack.enter_context(patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")))
    stack.enter_context(patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")))
    stack.enter_context(patch.object(strat.ind, "fvg_bonus", return_value=(0, None)))
    stack.enter_context(patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)))
    stack.enter_context(patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]))
    leg = dict(_FAKE_LEG) if find_leg_return is _UNSET else find_leg_return
    stack.enter_context(patch.object(strat, "find_leg", return_value=leg))
    stack.enter_context(patch.object(strat.market_sessions, "session_range", return_value=(None, None)))
    stack.enter_context(patch.object(strat.market_sessions, "daily_open", return_value=None))
    stack.enter_context(patch.object(strat.market_sessions, "weekly_open", return_value=None))


def test_diagnostic_mode_qualifying_setup_has_no_blocked_reason():
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    import contextlib
    import datetime as dt
    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store(), diagnostic=True)
    assert result["blocked"] is None
    assert result["score"] >= 75
    assert result["pattern"] == "LIQUIDITY_SWEEP_BOS"


def test_score_candidate_skipped_when_no_bos_confirmed():
    """If find_leg can't confirm a BOS anywhere in the recent window, the
    setup is skipped entirely (not sent with a nonsensical entry)."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    import contextlib
    import datetime as dt
    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack, find_leg_return=None)
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store())
        diag = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store(), diagnostic=True)
    assert result is None
    assert diag["blocked"] == "no confirmed BOS in recent history"


def test_score_candidate_emits_two_fixed_r_targets():
    """v3.2 integration check: score_candidate emits exactly two targets, TP1
    at 2R and TP2 at 3R off the (clamped) risk, and no third runner tier."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    import contextlib
    import datetime as dt
    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc), _fake_level_store())
    assert result is not None
    assert result["tp3"] is None
    assert result["tp1_basis"] == "2.0R"
    assert result["tp2_basis"] == "3.0R"
    entry, stop = result["entry_price"], result["stop_loss"]
    risk = abs(entry - stop)
    assert abs(result["tp1"] - (entry + 2 * risk)) < 1e-3
    assert abs(result["tp2"] - (entry + 3 * risk)) < 1e-3


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
                 "sweep_price": 100.0, "quality": 38}
    import contextlib
    import datetime as dt
    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        scored = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store(), diagnostic=True)
    diagnostic_entry = {"pattern": scored["pattern"], "direction": scored["direction"],
                         "score": scored["score"], "blocked": scored["blocked"]}
    assert diagnostic_entry["pattern"] == "LIQUIDITY_SWEEP_BOS"


def test_score_candidate_blocks_on_excessive_volatility():
    """v3.2 §8: ATR/price above the class ceiling (1.8% for indices) is a hard
    block before any scoring -- the market is too wild for structural stops."""
    import contextlib
    import datetime as dt
    import pandas as pd
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    now = dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc)
    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        # ATR ~5 on a ~100 price = 5% -> above the 1.8% index ceiling.
        stack.enter_context(patch.object(strat.ind, "atr", return_value=pd.Series([5.0] * 80)))
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store(), diagnostic=True)
    assert result["score"] is None
    assert "volatility" in result["blocked"]


def _ranging_market(quality):
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": make_candles(260, start_price=100.0, step=0.0, noise=0.5, interval_minutes=240),
    }
    candidate = {"pattern": "FLAG", "direction": "BUY", "sweep_price": 100.0, "quality": quality}
    return market, candidate


def test_score_candidate_loose_mode_lower_watch_threshold():
    """A setup scoring ~58 (RANGING bias +5, quality 27) must be blocked under
    the default 62 threshold but pass under loose mode's 55 threshold."""
    import contextlib
    import datetime as dt
    market, candidate = _ranging_market(quality=27)
    now = dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc)
    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        stack.enter_context(patch.object(strat.ind, "volume_profile_zones", return_value=(None, None, None)))
        default_result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store())
        loose_result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store(), mode=modes.LOOSE)
    assert default_result is None
    assert loose_result is not None
    assert loose_result["score"] == 58


def test_score_candidate_diagnostic_blocked_message_reflects_mode_threshold():
    import contextlib
    import datetime as dt
    market, candidate = _ranging_market(quality=10)
    now = dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc)
    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        stack.enter_context(patch.object(strat.ind, "volume_profile_zones", return_value=(None, None, None)))
        default_result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store(), diagnostic=True)
        loose_result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store(),
            diagnostic=True, mode=modes.LOOSE)
    assert "72" in default_result["blocked"]
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
                 "sweep_price": 100.0, "quality": 38}
    import contextlib
    import datetime as dt
    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc),
            _fake_level_store())
    assert result is not None
    assert result["direction"] == "BUY"
    assert result["score"] >= 75


def _c(o, h, l, cl):
    return {"o": o, "h": h, "l": l, "c": cl, "v": None}


def _buy_bos_candles():
    """A clean, isolated BUY setup: swing low fractal at idx3 (94), swing
    high fractal at idx6 (108), a sweep to a new low (90) at idx10, and a
    BOS candle at idx11 closing (110) above the swing high."""
    return [
        _c(100, 100.2, 99.8, 100), _c(100, 100.3, 99.7, 100), _c(100, 100.4, 99.6, 100),
        _c(100, 100.2, 94.0, 100),
        _c(100, 100.3, 99.5, 100), _c(100, 100.1, 99.4, 100),
        _c(100, 108.0, 99.6, 100),
        _c(100, 100.2, 99.3, 100), _c(100, 100.4, 99.9, 100), _c(100, 100.1, 99.2, 100),
        _c(100, 101.0, 90.0, 95),
        _c(95, 112.0, 94.0, 110),
    ]


def _sell_bos_candles():
    """Mirror of _buy_bos_candles: swing high fractal at idx3 (106), swing
    low fractal at idx6 (92), a sweep to a new high (110) at idx10, and a
    BOS candle at idx11 closing (90) below the swing low."""
    return [
        _c(100, 100.2, 99.8, 100), _c(100, 100.3, 99.7, 100), _c(100, 100.4, 99.6, 100),
        _c(100, 106.0, 99.8, 100),
        _c(100, 100.3, 99.5, 100), _c(100, 100.1, 99.4, 100),
        _c(100, 100.2, 92.0, 100),
        _c(100, 100.2, 99.3, 100), _c(100, 100.4, 99.9, 100), _c(100, 100.1, 99.2, 100),
        _c(100, 110.0, 99.0, 105),
        _c(105, 106.0, 88.0, 90),
    ]


def test_find_leg_buy_locates_sweep_and_bos():
    leg = strat.find_leg(_buy_bos_candles(), "BUY")
    assert leg == {"leg_origin": 90.0, "leg_end": 112.0, "bos_index": 11}


def test_find_leg_sell_locates_sweep_and_bos():
    leg = strat.find_leg(_sell_bos_candles(), "SELL")
    assert leg == {"leg_origin": 110.0, "leg_end": 88.0, "bos_index": 11}


def test_score_candidate_applies_whale_flow_bonus_for_btcusd_only():
    """The whale-flow confirmation bonus (strategy/whale_tracker.py) is only
    meaningful for the one on-chain instrument tracked; every other
    instrument has no exchange-netflow signal to speak of."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    import contextlib
    import datetime as dt
    now = dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc)

    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        stack.enter_context(patch.object(
            strat.whale_tracker, "whale_flow_bonus", return_value=(8, "whale_accumulation")))
        btc_result = strat.score_candidate(
            "BTCUSD", "CRYPTO", candidate, market, now, _fake_level_store(),
            diagnostic=True, whale_transactions=["some-tx"])
        other_result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store(),
            diagnostic=True, whale_transactions=["some-tx"])

    assert btc_result["breakdown"]["whale_flow"] == "whale_accumulation"
    assert "whale_flow" not in other_result["breakdown"]
    assert btc_result["score"] - other_result["score"] == 8


def test_score_candidate_btcusd_whale_flow_defaults_to_no_bonus_without_transactions():
    """whale_transactions defaults to None -- must never raise, and with no
    real data the netflow is neutral so no bonus applies."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    import contextlib
    import datetime as dt
    now = dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc)

    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        result = strat.score_candidate(
            "BTCUSD", "CRYPTO", candidate, market, now, _fake_level_store(), diagnostic=True)

    assert result["breakdown"]["whale_flow"] is None


def test_find_leg_returns_none_without_a_confirmed_bos():
    flat = [_c(100, 101, 99, 100)] * 10
    assert strat.find_leg(flat, "BUY") is None


def test_find_leg_returns_the_most_recent_bos():
    """Two independent BOS events in the same window -- find_leg must return
    the later (more recent) one, not the first it happens to encounter."""
    first = _buy_bos_candles()
    # Append a second, later sweep+BOS sequence further above the first.
    second = [
        _c(110, 110.2, 109.8, 110), _c(110, 110.3, 109.7, 110), _c(110, 110.4, 109.6, 110),
        _c(110, 118.0, 109.8, 110),
        _c(110, 110.3, 109.5, 110), _c(110, 110.1, 109.4, 110),
        _c(110, 110.2, 102.0, 110),
        _c(110, 110.2, 109.3, 110), _c(110, 110.4, 109.9, 110), _c(110, 110.1, 109.2, 110),
        _c(110, 111.0, 100.0, 105),
        _c(105, 122.0, 104.0, 120),
    ]
    leg = strat.find_leg(first + second, "BUY")
    assert leg["bos_index"] == len(first) + 11
    assert leg["leg_origin"] == 100.0
    assert leg["leg_end"] == 122.0


def test_compute_entry_default_50_pct_retrace():
    entry, basis = strat.compute_entry(90.0, 100.0, "BUY")
    assert entry == 95.0
    assert basis == "50% leg retrace"


def test_compute_entry_sell_mirrors_buy():
    entry, basis = strat.compute_entry(110.0, 100.0, "SELL")
    assert entry == 105.0
    assert basis == "50% leg retrace"


def test_compute_entry_fvg_midpoint_override_inside_zone():
    """FVG fully inside the leg with its midpoint in the 40-62% retrace zone
    (here 95.0 = exactly 50%) must override the raw retrace entry."""
    fvg_zones = [{"direction": "BULLISH", "bottom": 94.0, "top": 96.0, "index": 0}]
    entry, basis = strat.compute_entry(90.0, 100.0, "BUY", fvg_zones=fvg_zones)
    assert entry == 95.0
    assert basis == "FVG midpoint"


def test_compute_entry_ignores_fvg_outside_the_40_62_zone():
    """An FVG fully inside the leg but whose midpoint sits outside the
    40-62% retrace band (here near 90%) must NOT override the entry."""
    fvg_zones = [{"direction": "BULLISH", "bottom": 90.5, "top": 91.5, "index": 0}]
    entry, basis = strat.compute_entry(90.0, 100.0, "BUY", fvg_zones=fvg_zones)
    assert entry == 95.0
    assert basis == "50% leg retrace"


def test_compute_entry_ignores_fvg_not_fully_inside_the_leg():
    fvg_zones = [{"direction": "BULLISH", "bottom": 85.0, "top": 96.0, "index": 0}]  # bottom is outside [90,100]
    entry, basis = strat.compute_entry(90.0, 100.0, "BUY", fvg_zones=fvg_zones)
    assert entry == 95.0
    assert basis == "50% leg retrace"


def test_compute_stop_us100_worked_example():
    """v3.2 §7.1: sweep low 26,850, ATR=20, spread=2 -> buffer=max(20,6)=20 ->
    SL=26,830 (nearest 100-multiple is 26,800, 30pts away -> no round offset)."""
    stop = strat.compute_stop(26850.0, "BUY", atr_value=20.0, spread=2.0, instrument="US100")
    assert stop == 26830.0


def test_compute_stop_eurusd_worked_example():
    """ATR=0.00120, spread=0.00006 -> buffer=max(0.00120,0.00018)=0.00120 ->
    SL=1.17380; nearest 50-pip level (1.17500) is 12 pips away, clears the
    3-pip round-number threshold."""
    stop = strat.compute_stop(1.17500, "BUY", atr_value=0.00120, spread=0.00006, instrument="EURUSD")
    assert round(stop, 5) == 1.17380


def test_compute_stop_applies_round_number_offset_when_too_close():
    """US500: round multiple 50, proximity 3. A raw SL landing within 3 pts
    of a 50-multiple must get pushed an extra 0.15xATR further away."""
    # leg_origin=5111, buffer=max(1.0*10,0)=10 -> raw stop=5101, only 1pt from
    # the nearest 50-multiple (5100) -- inside the 3pt threshold.
    raw = 5111.0 - 10.0
    assert raw == 5101.0
    nearest = round(raw / 50) * 50
    assert nearest == 5100
    assert abs(raw - nearest) <= 3  # confirms this scenario actually triggers the offset

    stop = strat.compute_stop(5111.0, "BUY", atr_value=10.0, spread=0.0, instrument="US500")
    assert stop == raw - strat.cfg.ROUND_NUMBER_OFFSET_ATR_MULT * 10.0


def test_clamp_stop_distance_widens_a_too_tight_stop():
    # entry 100, stop 99 -> dist 1; min = 2xATR(2) = 4 -> stop pushed out to 96.
    stop = strat.clamp_stop_distance(100.0, 99.0, "BUY", atr_value=2.0, instrument="US500")
    assert stop == 96.0


def test_clamp_stop_distance_tightens_a_too_wide_stop():
    # entry 100, stop 80 -> dist 20; max = 4xATR(2) = 8 -> stop pulled in to 92.
    stop = strat.clamp_stop_distance(100.0, 80.0, "BUY", atr_value=2.0, instrument="US500")
    assert stop == 92.0


def test_clamp_stop_distance_btc_uses_tighter_ceiling():
    # BTC max = 3.5xATR. entry 100, stop 60 -> dist 40; ATR 10 -> max 35 -> stop 65.
    stop = strat.clamp_stop_distance(100.0, 60.0, "BUY", atr_value=10.0, instrument="BTCUSD")
    assert stop == 65.0


def test_compute_tp1_is_two_r():
    tp1, basis = strat.compute_tp1("BUY", entry=100.0, risk=10.0)
    assert tp1 == 120.0
    assert basis == "2.0R"
    tp1_sell, _ = strat.compute_tp1("SELL", entry=100.0, risk=10.0)
    assert tp1_sell == 80.0


def test_compute_tp2_is_three_r():
    tp2, basis = strat.compute_tp2("BUY", entry=100.0, risk=10.0)
    assert tp2 == 130.0
    assert basis == "3.0R"
    tp2_sell, _ = strat.compute_tp2("SELL", entry=100.0, risk=10.0)
    assert tp2_sell == 70.0


def test_worked_example_us100_long():
    """Strategy v3.2 acceptance test: US100 long, two fixed R-multiple targets."""
    leg_origin, leg_end = 26850.0, 26950.0
    atr_value, spread = 20.0, 2.0

    entry, entry_basis = strat.compute_entry(leg_origin, leg_end, "BUY")
    assert entry == 26900.0
    assert entry_basis == "50% leg retrace"

    stop = strat.compute_stop(leg_origin, "BUY", atr_value, spread, "US100")
    stop = strat.clamp_stop_distance(entry, stop, "BUY", atr_value, "US100")
    assert stop == 26830.0
    risk = entry - stop
    assert risk == 70.0  # 3.5xATR -- inside the [2,4]xATR band, unchanged by the clamp

    tp1, tp1_basis = strat.compute_tp1("BUY", entry, risk)
    assert tp1 == 27040.0 and tp1_basis == "2.0R"   # entry + 2R
    tp2, tp2_basis = strat.compute_tp2("BUY", entry, risk)
    assert tp2 == 27110.0 and tp2_basis == "3.0R"   # entry + 3R


def test_worked_example_eurusd_long():
    """Strategy v3.2 acceptance test: EURUSD long."""
    leg_origin, leg_end = 1.17500, 1.17900
    atr_value, spread = 0.00120, 0.00006

    entry, _ = strat.compute_entry(leg_origin, leg_end, "BUY")
    assert round(entry, 5) == 1.17700

    stop = strat.compute_stop(leg_origin, "BUY", atr_value, spread, "EURUSD")
    stop = strat.clamp_stop_distance(entry, stop, "BUY", atr_value, "EURUSD")
    assert round(stop, 5) == 1.17380
    risk = entry - stop
    assert round(risk, 5) == 0.00320  # 2.667xATR -- inside the band

    tp1, _ = strat.compute_tp1("BUY", entry, risk)
    assert round(tp1, 5) == 1.18340   # entry + 2R
    tp2, _ = strat.compute_tp2("BUY", entry, risk)
    assert round(tp2, 5) == 1.18660   # entry + 3R


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


def test_multiframe_alignment_reports_per_timeframe_agreement():
    up = make_candles(60, start_price=100.0, step=1.0, noise=0.0)
    down = make_candles(60, start_price=300.0, step=-1.0, noise=0.0)
    tf = strat.multiframe_alignment(up, up, down, "BUY")
    assert tf["15m"]["trend"] == "up" and tf["15m"]["agree"] == "aligned"
    assert tf["1h"]["agree"] == "aligned"
    assert tf["4h"]["trend"] == "down" and tf["4h"]["agree"] == "against"


def test_multiframe_alignment_sell_direction_flips_agreement():
    down = make_candles(60, start_price=300.0, step=-1.0, noise=0.0)
    tf = strat.multiframe_alignment(down, down, down, "SELL")
    assert tf["1h"]["trend"] == "down" and tf["1h"]["agree"] == "aligned"


def test_multiframe_alignment_flat_without_enough_history():
    tf = strat.multiframe_alignment([], [], [], "BUY")
    assert tf["15m"] == {"trend": "flat", "agree": "flat"}
    assert tf["4h"] == {"trend": "flat", "agree": "flat"}
