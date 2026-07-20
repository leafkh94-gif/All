from unittest.mock import patch

import pandas as pd

import scoring_indicators as ind
from strategy import modes
from tests.helpers import make_candles


def test_rsi_high_for_strong_uptrend():
    candles = make_candles(60, start_price=100.0, step=1.0, noise=0.0)
    df = pd.DataFrame(candles)
    assert ind.rsi(df["c"]).iloc[-1] > 70


def test_rsi_low_for_strong_downtrend():
    candles = make_candles(60, start_price=100.0, step=-1.0, noise=0.0)
    df = pd.DataFrame(candles)
    assert ind.rsi(df["c"]).iloc[-1] < 30


def test_atr_positive_when_ranges_exist():
    candles = make_candles(30, start_price=100.0, step=0.5, noise=1.0)
    df = pd.DataFrame(candles)
    assert ind.atr(df).iloc[-1] > 0


def test_atr_percentile_short_series_defaults_to_50():
    df = pd.DataFrame(make_candles(1))
    assert ind.atr_percentile(df) == 50.0


def test_round_number_bonus_near_level():
    assert ind.round_number_bonus(6000.5, "US_INDEX") == 5


def test_round_number_bonus_far_from_level():
    assert ind.round_number_bonus(6250.0, "US_INDEX") == 0


def test_round_number_bonus_btc():
    assert ind.round_number_bonus(100010.0, "CRYPTO") == 5


def test_atr_sweet_spot_penalty_uses_mode_percentiles():
    """A percentile between standard's 80th and loose's 85th high bound must be
    'too_volatile' under standard mode but 'normal' under loose mode."""
    df = pd.DataFrame(make_candles(30, start_price=100.0, step=0.5, noise=1.0))
    with patch.object(ind, "atr_percentile", return_value=82.0):
        std_penalty, std_state = ind.atr_sweet_spot_penalty(df, mode=modes.STANDARD)
        loose_penalty, loose_state = ind.atr_sweet_spot_penalty(df, mode=modes.LOOSE)
    assert std_state == "too_volatile"
    assert std_penalty < 0
    assert loose_state == "normal"
    assert loose_penalty == 0


def test_atr_sweet_spot_penalty_defaults_to_standard_mode():
    df = pd.DataFrame(make_candles(30, start_price=100.0, step=0.5, noise=1.0))
    with patch.object(ind, "atr_percentile", return_value=82.0):
        penalty, state = ind.atr_sweet_spot_penalty(df)
    assert state == "too_volatile"
    assert penalty < 0


def test_level_store_roundtrip(tmp_path):
    store = ind.LevelStore(path=str(tmp_path / "levels.json"))
    store.set_daily_levels("US500", 5100.0, 5000.0, "2026-07-01")
    loaded = store.get_daily_levels("US500")
    assert loaded == {"high": 5100.0, "low": 5000.0, "day_key": "2026-07-01"}

    store.set_weekly_levels("US500", 5200.0, 4900.0, "2026-W27")
    assert store.get_weekly_levels("US500")["high"] == 5200.0


def test_pdh_pdl_bonus_within_proximity():
    pts, tag = ind.pdh_pdl_bonus(5100.0, pdh=5100.5, pdl=5000.0)
    assert pts == 10 and tag == "PDH"


def test_pdh_pdl_bonus_no_match():
    pts, tag = ind.pdh_pdl_bonus(5050.0, pdh=5100.0, pdl=5000.0)
    assert pts == 0 and tag is None


def test_monday_weekly_sweep_bonus_gated_by_weekday():
    import datetime as dt
    tuesday = dt.datetime(2026, 6, 30, 10, 0, tzinfo=dt.timezone.utc)  # a Tuesday
    pts, tag = ind.monday_weekly_sweep_bonus(5100.0, 5100.0, 4900.0, tuesday)
    assert pts == 0 and tag is None


def test_monday_weekly_sweep_bonus_fires_on_monday():
    import datetime as dt
    monday = dt.datetime(2026, 6, 29, 10, 0, tzinfo=dt.timezone.utc)  # a Monday
    pts, tag = ind.monday_weekly_sweep_bonus(5100.0, 5100.2, 4900.0, monday)
    assert pts == 12 and tag == "WEEK_HIGH"


def test_detect_fvg_zones_bullish_gap():
    candles = [
        {"o": 100, "h": 101, "l": 99, "c": 100.5},
        {"o": 100.5, "h": 108, "l": 100, "c": 107},   # explosive candle
        {"o": 107, "h": 109, "l": 106, "c": 108},     # low (106) > candle0 high (101) -> bullish FVG
    ]
    zones = ind.detect_fvg_zones(candles, sweep_index=2, max_lookback=3)
    assert any(z["direction"] == "BULLISH" for z in zones)


def test_fvg_bonus_hits_when_entry_inside_zone():
    candles = [
        {"o": 100, "h": 101, "l": 99, "c": 100.5},
        {"o": 100.5, "h": 108, "l": 100, "c": 107},
        {"o": 107, "h": 109, "l": 106, "c": 108},
    ]
    pts, zone = ind.fvg_bonus(103.0, "BUY", candles, sweep_index=2)
    assert pts == 8 and zone is not None


def test_detect_ifvg_zones_flips_bullish_fvg_to_bearish_after_invalidation():
    candles = [
        {"o": 100, "h": 101, "l": 99, "c": 100.5},    # c0
        {"o": 100.5, "h": 108, "l": 100, "c": 107},   # c1
        {"o": 107, "h": 109, "l": 106, "c": 108},      # c2 -> bullish FVG (bottom=101, top=106)
        {"o": 108, "h": 108, "l": 95, "c": 96},        # closes below 101 -> invalidates, flips BEARISH
    ]
    zones = ind.detect_ifvg_zones(candles, sweep_index=3, max_lookback=4)
    assert any(z["direction"] == "BEARISH" and z["bottom"] == 101.0 and z["top"] == 106.0 for z in zones)


def test_detect_ifvg_zones_empty_when_fvg_never_invalidated():
    candles = [
        {"o": 100, "h": 101, "l": 99, "c": 100.5},
        {"o": 100.5, "h": 108, "l": 100, "c": 107},
        {"o": 107, "h": 109, "l": 106, "c": 108},
        {"o": 108, "h": 110, "l": 107, "c": 109},   # stays well above the FVG -- never invalidated
    ]
    zones = ind.detect_ifvg_zones(candles, sweep_index=3, max_lookback=4)
    assert zones == []


def test_ifvg_bonus_hits_when_entry_inside_flipped_zone():
    candles = [
        {"o": 100, "h": 101, "l": 99, "c": 100.5},
        {"o": 100.5, "h": 108, "l": 100, "c": 107},
        {"o": 107, "h": 109, "l": 106, "c": 108},
        {"o": 108, "h": 108, "l": 95, "c": 96},
    ]
    pts, zone = ind.ifvg_bonus(103.0, "SELL", candles, sweep_index=3)
    assert pts == 8 and zone is not None


def test_ifvg_bonus_no_match_without_invalidation():
    candles = [
        {"o": 100, "h": 101, "l": 99, "c": 100.5},
        {"o": 100.5, "h": 108, "l": 100, "c": 107},
        {"o": 107, "h": 109, "l": 106, "c": 108},
    ]
    pts, zone = ind.ifvg_bonus(103.0, "SELL", candles, sweep_index=2)
    assert pts == 0 and zone is None


def test_detect_eqh_eql_zones_finds_equal_highs():
    candles = []
    base = [100, 101, 105, 101, 100, 101, 105.02, 101, 100]
    for i, price in enumerate(base):
        candles.append({"o": price, "h": price + 0.5, "l": price - 0.5, "c": price})
    zones = ind.detect_eqh_eql_zones(candles, lookback=len(candles), tolerance_pct=0.01)
    assert any(z["type"] == "EQH" for z in zones)


def test_eqh_eql_bonus_matches_zone():
    zones = [{"type": "EQH", "price": 105.5, "touches": 2}]
    pts, zone = ind.eqh_eql_bonus(105.6, zones, tolerance_pct=0.01)
    assert pts == 10 and zone is not None


def test_anchored_vwap_returns_none_without_volume():
    df = pd.DataFrame(make_candles(10))  # helper always sets v=None
    assert ind.anchored_vwap(df) is None


def test_anchored_vwap_computes_volume_weighted_average():
    import datetime as dt
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    candles = [
        {"t": "2026-07-01T09:00:00", "o": 100, "h": 100, "l": 100, "c": 100, "v": 10},
        {"t": "2026-07-01T10:00:00", "o": 110, "h": 110, "l": 110, "c": 110, "v": 30},
    ]
    df = pd.DataFrame(candles)
    vwap = ind.anchored_vwap(df, now_utc=now)
    assert vwap == 107.5  # (100*10 + 110*30) / 40, h=l=c so typical price == close


def test_anchored_vwap_ignores_candles_before_todays_anchor():
    import datetime as dt
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    candles = [
        {"t": "2026-06-30T23:00:00", "o": 500, "h": 500, "l": 500, "c": 500, "v": 1000},  # yesterday
        {"t": "2026-07-01T01:00:00", "o": 100, "h": 100, "l": 100, "c": 100, "v": 10},
    ]
    df = pd.DataFrame(candles)
    vwap = ind.anchored_vwap(df, now_utc=now)
    assert vwap == 100.0  # only today's candle counted


def test_volume_profile_zones_no_volume_returns_none():
    df = pd.DataFrame(make_candles(10))
    assert ind.volume_profile_zones(df) == (None, None, None)


def test_volume_profile_zones_poc_near_high_volume_price():
    candles = [
        {"o": 100, "h": 101, "l": 99, "c": 100, "v": 5},
        {"o": 100, "h": 101, "l": 99, "c": 100, "v": 100},   # heavy volume at 99-101 -> POC here
        {"o": 120, "h": 121, "l": 119, "c": 120, "v": 5},
    ]
    df = pd.DataFrame(candles)
    poc, va_low, va_high = ind.volume_profile_zones(df, num_bins=10)
    assert 99 <= poc <= 101
    assert va_low <= poc <= va_high


def test_volume_profile_bonus_inside_value_area():
    pts, tag = ind.volume_profile_bonus(100.0, poc=100.0, va_low=99.0, va_high=101.0)
    assert pts == 3 and tag == "in_value_area"


def test_volume_profile_bonus_outside_value_area():
    pts, tag = ind.volume_profile_bonus(150.0, poc=100.0, va_low=99.0, va_high=101.0)
    assert pts == 0 and tag is None


def test_volume_profile_bonus_no_data():
    pts, tag = ind.volume_profile_bonus(100.0, None, None, None)
    assert pts == 0 and tag is None


def test_cap_tp2_at_liquidity_caps_buy_when_raw_tp2_exceeds_pdh():
    capped, was_capped = ind.cap_tp2_at_liquidity("BUY", entry=100.0, tp2=110.0, pdh=105.0, pdl=90.0,
                                                    pwh=None, pwl=None)
    assert was_capped is True
    assert capped == 105.0


def test_cap_tp2_at_liquidity_uses_nearest_of_pdh_and_pwh():
    capped, was_capped = ind.cap_tp2_at_liquidity("BUY", entry=100.0, tp2=110.0, pdh=108.0, pdl=None,
                                                    pwh=104.0, pwl=None)
    assert was_capped is True
    assert capped == 104.0


def test_cap_tp2_at_liquidity_no_cap_when_raw_tp2_already_inside_level():
    capped, was_capped = ind.cap_tp2_at_liquidity("BUY", entry=100.0, tp2=103.0, pdh=105.0, pdl=None,
                                                    pwh=None, pwl=None)
    assert was_capped is False
    assert capped == 103.0


def test_cap_tp2_at_liquidity_no_levels_available_returns_uncapped():
    capped, was_capped = ind.cap_tp2_at_liquidity("BUY", entry=100.0, tp2=110.0, pdh=None, pdl=None,
                                                    pwh=None, pwl=None)
    assert was_capped is False
    assert capped == 110.0


def test_cap_tp2_at_liquidity_caps_sell_when_raw_tp2_undershoots_pdl():
    capped, was_capped = ind.cap_tp2_at_liquidity("SELL", entry=100.0, tp2=88.0, pdh=None, pdl=93.0,
                                                    pwh=None, pwl=None)
    assert was_capped is True
    assert capped == 93.0


def test_cap_tp2_at_liquidity_ignores_levels_behind_entry():
    """A PDH below current entry (already swept) isn't a valid forward target."""
    capped, was_capped = ind.cap_tp2_at_liquidity("BUY", entry=100.0, tp2=110.0, pdh=99.0, pdl=None,
                                                    pwh=None, pwl=None)
    assert was_capped is False
    assert capped == 110.0
