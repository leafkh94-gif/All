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
    with patch.object(strat, "vwap_filter_score", return_value=0), \
         patch.object(strat, "choppiness_index", return_value=0.0), \
         patch.object(strat.market_sessions, "killzone_bonus", return_value=(0, "NONE")), \
         patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")), \
         patch.object(strat.ind, "liquidity_confluence_bonus", return_value=(0, [])), \
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
    # v2 factor set: technical_confirm and volume_profile removed; liquidity is
    # one grouped bonus; choppy is a gate (choppiness_index), not a penalty.
    stack.enter_context(patch.object(strat, "vwap_filter_score", return_value=4))
    stack.enter_context(patch.object(strat, "choppiness_index", return_value=0.0))  # choppy gate passes
    stack.enter_context(patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")))
    stack.enter_context(patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")))
    stack.enter_context(patch.object(strat.ind, "liquidity_confluence_bonus", return_value=(12, ["PDH", "EQH"])))
    stack.enter_context(patch.object(strat.ind, "fvg_bonus", return_value=(8, {"bottom": 0, "top": 1})))
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


def test_score_candidate_wires_pooled_tp2_level():
    """Integration check: score_candidate threads a real level-store PDH
    into the TP2 pool and caps TP2 there, ahead of the raw 1.8R fallback."""
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": trending_h4_candles(up=True),
    }
    candidate = {"pattern": "LIQUIDITY_SWEEP_BOS", "direction": "BUY",
                 "sweep_price": 100.0, "quality": 38}
    import contextlib
    import datetime as dt

    class _Store:
        def get_daily_levels(self, instrument):
            return {"high": 108.0, "low": 90.0}

        def get_weekly_levels(self, instrument):
            return None

    with contextlib.ExitStack() as stack:
        _patch_qualifying_stack(stack)
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market,
            dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc), _Store())
    assert result is not None
    # leg_origin=95, leg_end=100.5 (mocked) -> entry=97.75, well below PDH=108,
    # and the raw 1.8R target from that entry/risk is nowhere near 108 either
    # -- PDH still ends up nearest and caps TP2.
    assert result["tp2_capped"] is True
    assert result["tp2"] == 108.0


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


def _ranging_market(quality):
    market = {
        "entry": make_candles(80, start_price=100.0, noise=0.3),
        "h1": make_candles(160, start_price=100.0, noise=0.3, interval_minutes=60),
        "h4": make_candles(260, start_price=100.0, step=0.0, noise=0.5, interval_minutes=240),
    }
    candidate = {"pattern": "FLAG", "direction": "BUY", "sweep_price": 100.0, "quality": quality}
    return market, candidate


def test_score_candidate_loose_mode_lower_watch_threshold():
    """A setup scoring 58 (RANGING bias +5, quality 17, + pinned vwap4/kz12/
    liq12/fvg8 = 58) must be blocked under the default 62 threshold but pass
    under loose mode's 55 threshold."""
    import contextlib
    import datetime as dt
    market, candidate = _ranging_market(quality=17)
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
    assert "62" in default_result["blocked"]
    assert "55" in loose_result["blocked"]


def test_independence_gate_blocks_setup_with_no_context_axis():
    """v2: a setup with structure (BOS) + timing (killzone) but NO context
    (no liquidity confluence, no FVG/IFVG, and only a neutral/ranging bias)
    is blocked by the 3-axis independence gate even if it clears the score."""
    import contextlib
    import datetime as dt
    market, candidate = _ranging_market(quality=60)  # high quality so score alone would pass
    now = dt.datetime(2026, 1, 1, 12, 45, tzinfo=dt.timezone.utc)
    with contextlib.ExitStack() as stack:
        # timing axis present (killzone), but NO context: liq empty, no fvg/ifvg,
        # ranging bias (not with-trend).
        stack.enter_context(patch.object(strat, "vwap_filter_score", return_value=0))
        stack.enter_context(patch.object(strat, "choppiness_index", return_value=0.0))
        stack.enter_context(patch.object(strat.market_sessions, "killzone_bonus", return_value=(12, "NY_KILLZONE")))
        stack.enter_context(patch.object(strat.ind, "atr_sweet_spot_penalty", return_value=(0, "normal")))
        stack.enter_context(patch.object(strat.ind, "liquidity_confluence_bonus", return_value=(0, [])))
        stack.enter_context(patch.object(strat.ind, "fvg_bonus", return_value=(0, None)))
        stack.enter_context(patch.object(strat.ind, "ifvg_bonus", return_value=(0, None)))
        stack.enter_context(patch.object(strat.ind, "detect_eqh_eql_zones", return_value=[]))
        stack.enter_context(patch.object(strat, "find_leg", return_value=dict(_FAKE_LEG)))
        stack.enter_context(patch.object(strat.market_sessions, "session_range", return_value=(None, None)))
        stack.enter_context(patch.object(strat.market_sessions, "daily_open", return_value=None))
        stack.enter_context(patch.object(strat.market_sessions, "weekly_open", return_value=None))
        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, _fake_level_store(), diagnostic=True)
    assert "insufficient independent confluence" in result["blocked"]


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
    """v2 buffer = max(1.0xATR, 3xspread): sweep low 26,850, ATR=20, spread=2 ->
    buffer=max(20,6)=20 -> SL=26,830 (no round-number collision)."""
    stop = strat.compute_stop(26850.0, "BUY", atr_value=20.0, spread=2.0, instrument="US100")
    assert stop == 26830.0


def test_compute_stop_eurusd_worked_example():
    """v2: ATR=0.00120, spread=0.00006 -> buffer=max(0.00120,0.00018)=0.00120 ->
    SL=1.17380; round check vs 1.17500 (12 pips) clears the 3-pip threshold."""
    stop = strat.compute_stop(1.17500, "BUY", atr_value=0.00120, spread=0.00006, instrument="EURUSD")
    assert round(stop, 5) == 1.17380


def test_compute_stop_applies_round_number_offset_when_too_close():
    """US500: round multiple 50, proximity 3. A raw SL landing within 3 pts
    of a 50-multiple must get pushed an extra 0.15xATR further away."""
    # v2 buffer = max(1.0*10, 0) = 10. leg_origin=5108 -> raw stop=5098, only
    # 2pts from the nearest 50-multiple (5100) -- inside the 3pt threshold.
    raw = 5108.0 - 10.0
    assert raw == 5098.0
    nearest = round(raw / 50) * 50
    assert nearest == 5100
    assert abs(raw - nearest) <= 3  # confirms this scenario actually triggers the offset

    stop = strat.compute_stop(5108.0, "BUY", atr_value=10.0, spread=0.0, instrument="US500")
    assert stop == raw - strat.cfg.ROUND_NUMBER_OFFSET_ATR_MULT * 10.0


def test_compute_tp1_raw_one_r_without_exception_candidates():
    tp1, basis = strat.compute_tp1("BUY", entry=100.0, risk=10.0, fvg_zones=[], swing_prices=[])
    assert tp1 == 110.0
    assert basis == "1.0R"


def test_compute_tp1_swing_exception_inside_08_10_r_window():
    swing_prices = [108.5]  # 0.85R from entry -- inside [0.8R, 1.0R)
    tp1, basis = strat.compute_tp1("BUY", entry=100.0, risk=10.0, fvg_zones=[], swing_prices=swing_prices)
    assert tp1 == 108.5
    assert basis == "FVG/swing exception"


def test_compute_tp1_fvg_exception_uses_near_edge():
    fvg_zones = [{"direction": "BULLISH", "bottom": 109.0, "top": 111.0, "index": 0}]
    tp1, basis = strat.compute_tp1("BUY", entry=100.0, risk=10.0, fvg_zones=fvg_zones, swing_prices=[])
    assert tp1 == 109.0  # near edge (bottom) for a BUY
    assert basis == "FVG/swing exception"


def test_compute_tp1_ignores_candidates_outside_the_window():
    swing_prices = [107.0]  # 0.7R -- below the 0.8R floor
    tp1, basis = strat.compute_tp1("BUY", entry=100.0, risk=10.0, fvg_zones=[], swing_prices=swing_prices)
    assert tp1 == 110.0
    assert basis == "1.0R"


def test_compute_tp2_uses_nearest_level_ahead():
    tp2, from_level = strat.compute_tp2("BUY", entry=100.0, risk=10.0, levels=[108.0, 115.0])
    assert tp2 == 108.0
    assert from_level is True


def test_compute_tp2_falls_back_to_1_8r_without_a_level():
    tp2, from_level = strat.compute_tp2("BUY", entry=100.0, risk=10.0, levels=[])
    assert tp2 == 118.0
    assert from_level is False


def test_compute_tp2_rejects_a_pooled_level_between_entry_and_tp1():
    """Regression test for a real production bug: a pooled liquidity level
    merely 'ahead of entry' can land BETWEEN entry and TP1, which would
    make TP2 trigger before TP1 in real price action. Confirmed against
    live AUDUSD/JP225 alerts where TP1 ended up farther from entry than
    both TP2 and TP3. 105 is ahead of entry (100) but not ahead of TP1
    (110) -- it must be rejected, falling back to the 1.8R raw target."""
    tp2, from_level = strat.compute_tp2("BUY", entry=100.0, risk=10.0, levels=[105.0], tp1_price=110.0)
    assert tp2 == 118.0
    assert from_level is False


def test_compute_tp2_accepts_a_pooled_level_beyond_tp1_plus_separation():
    # v2: floor is TP1 + 0.5R = 110 + 5 = 115. A level at 112 is now rejected;
    # a level at 116 clears the separation and is accepted.
    tp2, from_level = strat.compute_tp2("BUY", entry=100.0, risk=10.0, levels=[112.0], tp1_price=110.0)
    assert tp2 == 118.0 and from_level is False   # 112 too close -> 1.8R fallback
    tp2, from_level = strat.compute_tp2("BUY", entry=100.0, risk=10.0, levels=[116.0], tp1_price=110.0)
    assert tp2 == 116.0 and from_level is True


def test_compute_tp2_sell_rejects_a_pooled_level_between_entry_and_tp1():
    tp2, from_level = strat.compute_tp2("SELL", entry=100.0, risk=10.0, levels=[95.0], tp1_price=90.0)
    assert tp2 == 82.0  # 1.8R fallback: 100 - 18
    assert from_level is False


def test_compute_tp2_sell_accepts_a_pooled_level_beyond_tp1_plus_separation():
    # v2: floor is TP1 - 0.5R = 90 - 5 = 85. A level at 88 is now rejected; 84 clears.
    tp2, from_level = strat.compute_tp2("SELL", entry=100.0, risk=10.0, levels=[88.0], tp1_price=90.0)
    assert tp2 == 82.0 and from_level is False   # 88 too close -> 1.8R fallback
    tp2, from_level = strat.compute_tp2("SELL", entry=100.0, risk=10.0, levels=[84.0], tp1_price=90.0)
    assert tp2 == 84.0 and from_level is True


def test_compute_tp3_prefers_whichever_is_farther_entry():
    # v2: TP3 = the FARTHER of raw 2.8R (=128) vs. the nearest external beyond TP2.
    # external at 150 is farther than raw -> external wins.
    tp3, from_level = strat.compute_tp3("BUY", entry=100.0, risk=10.0, tp2_price=108.0, levels=[150.0])
    assert tp3 == 150.0
    assert from_level is True
    # external at 120 is nearer than raw -> raw wins (runner not clipped short).
    tp3, from_level = strat.compute_tp3("BUY", entry=100.0, risk=10.0, tp2_price=108.0, levels=[120.0])
    assert tp3 == 128.0
    assert from_level is False


def test_compute_tp3_falls_back_to_2_8r_without_an_external_level():
    tp3, from_level = strat.compute_tp3("BUY", entry=100.0, risk=10.0, tp2_price=108.0, levels=[])
    assert tp3 == 128.0
    assert from_level is False


def test_worked_example_us100_long():
    """v2 acceptance test: US100 long (supersedes the v1.3 worked example --
    thicker 1.0xATR stop, TP2 needs TP1+0.5R separation, TP3 = farther-of)."""
    leg_origin, leg_end = 26850.0, 26950.0
    atr_value, spread = 20.0, 2.0

    entry, entry_basis = strat.compute_entry(leg_origin, leg_end, "BUY")
    assert entry == 26900.0
    assert entry_basis == "50% leg retrace"

    stop = strat.compute_stop(leg_origin, "BUY", atr_value, spread, "US100")
    assert stop == 26830.0   # v2: buffer = max(1.0*20, 3*2) = 20
    risk = entry - stop
    assert risk == 70.0

    tp1, _ = strat.compute_tp1("BUY", entry, risk, fvg_zones=[], swing_prices=[])
    assert tp1 == 26970.0    # entry + 1.0R

    # TP2 floor = TP1 + 0.5R = 26970 + 35 = 27005; a PDH at 27010 clears it.
    tp2_with_pdh, from_level = strat.compute_tp2("BUY", entry, risk, levels=[27010.0], tp1_price=tp1)
    assert tp2_with_pdh == 27010.0 and from_level is True
    tp2_fallback, from_level = strat.compute_tp2("BUY", entry, risk, levels=[], tp1_price=tp1)
    assert tp2_fallback == 27026.0 and from_level is False   # entry + 1.8R

    tp3, from_level = strat.compute_tp3("BUY", entry, risk, tp2_price=tp2_with_pdh, levels=[])
    assert tp3 == 27096.0    # entry + 2.8R fallback
    assert from_level is False


def test_worked_example_eurusd_long():
    """v2 acceptance test: EURUSD long (supersedes the v1.3 worked example)."""
    leg_origin, leg_end = 1.17500, 1.17900
    atr_value, spread = 0.00120, 0.00006

    entry, _ = strat.compute_entry(leg_origin, leg_end, "BUY")
    assert round(entry, 5) == 1.17700

    stop = strat.compute_stop(leg_origin, "BUY", atr_value, spread, "EURUSD")
    assert round(stop, 5) == 1.17380   # v2: buffer = max(1.0*0.00120, 3*0.00006) = 0.00120
    risk = entry - stop
    assert round(risk, 5) == 0.00320

    tp1, _ = strat.compute_tp1("BUY", entry, risk, fvg_zones=[], swing_prices=[])
    assert round(tp1, 5) == 1.18020    # entry + 1.0R

    tp3, from_level = strat.compute_tp3("BUY", entry, risk, tp2_price=entry, levels=[])
    assert round(tp3, 5) == 1.18596    # entry + 2.8R
    assert from_level is False


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
