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


def test_sma_matches_rolling_mean():
    candles = make_candles(30, start_price=100.0, step=1.0, noise=0.0)
    df = pd.DataFrame(candles)
    s = ind.sma(df["c"], 5)
    manual = df["c"].iloc[-5:].mean()
    assert abs(s.iloc[-1] - manual) < 1e-9


def test_zero_lag_sma_leads_ordinary_sma_in_a_trend():
    candles = make_candles(80, start_price=100.0, step=1.0, noise=0.0)
    df = pd.DataFrame(candles)
    s = ind.sma(df["c"], 20)
    z = ind.zero_lag_sma(df["c"], 20)
    assert z.iloc[-1] > s.iloc[-1]


def test_donchian_channels_bracket_recent_range():
    candles = make_candles(30, start_price=100.0, step=0.5, noise=2.0)
    df = pd.DataFrame(candles)
    upper, lower, mid = ind.donchian_channels(df, period=20)
    window = df.iloc[-20:]
    assert abs(upper.iloc[-1] - window["h"].max()) < 1e-9
    assert abs(lower.iloc[-1] - window["l"].min()) < 1e-9
    assert lower.iloc[-1] < mid.iloc[-1] < upper.iloc[-1]


def test_atr_positive_when_ranges_exist():
    candles = make_candles(30, start_price=100.0, step=0.5, noise=1.0)
    df = pd.DataFrame(candles)
    assert ind.atr(df).iloc[-1] > 0


def test_atr_percentile_short_series_defaults_to_50():
    df = pd.DataFrame(make_candles(1))
    assert ind.atr_percentile(df) == 50.0


def test_round_number_bonus_near_gold_level():
    # Gold table: (step=50, proximity=3). 2001.5 is 1.5 pts away from 2000.
    assert ind.round_number_bonus(2001.5, "XAUUSD") == 5


def test_round_number_bonus_far_from_gold_level():
    assert ind.round_number_bonus(2025.0, "XAUUSD") == 0


def test_round_number_bonus_unknown_instrument_returns_zero():
    assert ind.round_number_bonus(5000.0, "SOMETHING_ELSE") == 0


def test_atr_sweet_spot_penalty_flags_too_volatile():
    df = pd.DataFrame(make_candles(30, start_price=100.0, step=0.5, noise=1.0))
    with patch.object(ind, "atr_percentile", return_value=90.0):
        penalty, state = ind.atr_sweet_spot_penalty(df, mode=modes.STANDARD)
    assert state == "too_volatile"
    assert penalty < 0


def test_atr_sweet_spot_penalty_defaults_to_standard_mode():
    df = pd.DataFrame(make_candles(30, start_price=100.0, step=0.5, noise=1.0))
    with patch.object(ind, "atr_percentile", return_value=82.0):
        penalty, state = ind.atr_sweet_spot_penalty(df)
    assert state == "too_volatile"
    assert penalty < 0


def test_level_store_roundtrip(tmp_path):
    store = ind.LevelStore(path=str(tmp_path / "levels.json"))
    store.set_daily_levels("XAUUSD", 2100.0, 2050.0, "2026-07-01")
    loaded = store.get_daily_levels("XAUUSD")
    assert loaded == {"high": 2100.0, "low": 2050.0, "day_key": "2026-07-01"}

    store.set_weekly_levels("XAUUSD", 2200.0, 1980.0, "2026-W27")
    assert store.get_weekly_levels("XAUUSD")["high"] == 2200.0
