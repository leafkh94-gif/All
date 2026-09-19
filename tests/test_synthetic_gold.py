"""The synthetic generator has to actually look like gold.

Every distance constant in the config is now derived from statistics
measured on this generator, so if it drifts away from realistic gold
volatility those constants silently become wrong again — which is exactly
the failure that produced a $25 stop sitting at 14x ATR.
"""
import importlib.util
import os

import pandas as pd

import scoring_indicators as ind

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools", "make_synthetic_gold.py")
_spec = importlib.util.spec_from_file_location("make_synthetic_gold", _PATH)
gen_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen_mod)


def _df(bars=4000, **kw):
    return pd.DataFrame(gen_mod.generate(bars, **kw))


def test_m15_atr_lands_in_the_realistic_gold_band():
    """~$2-6 on M15 at a ~$2650 spot. The old plain random walk produced
    $1.74, which made every ATR-relative constant wrong. The band is
    centred on the MEASURED median M15 ATR of real XAUUSD, $8.76 over
    25,000 bars; an earlier band of 2.0-6.0 excluded the real value and
    so enforced a series 2.5x too quiet."""
    atr = ind.atr(_df()).dropna()
    assert 6.0 < atr.median() < 12.0, f"median ATR {atr.median():.2f} outside gold-like band"


def test_daily_range_is_a_realistic_fraction_of_spot():
    df = _df(6000)
    df["day"] = df["t"].str[:10]
    rng = df.groupby("day").apply(lambda g: g["h"].max() - g["l"].min(), include_groups=False)
    pct = 100 * rng.median() / df["c"].median()
    assert 0.7 < pct < 2.2, f"daily range {pct:.2f}% of spot is not gold-like"


def test_price_stays_in_a_plausible_band():
    """Unanchored trend drift walked price from 2650 to 473 over a long
    series, which destroys ATR-as-a-fraction-of-price.

    Expressed as a MULTIPLE of the starting spot, not as absolute dollar
    bounds: the old 1800-4200 was written for a 2650 spot and silently
    became wrong when spot moved. Real gold spanned 3405-5586 over the
    52-week sample, i.e. about 0.76x-1.24x its midpoint; 0.4x-1.6x leaves
    room for a longer series while still catching a runaway (the original
    failure was 0.18x)."""
    df = _df(12000)
    spot = gen_mod.DEFAULT_SPOT
    assert df["c"].min() > 0.4 * spot, "price wandered implausibly low"
    assert df["c"].max() < 1.6 * spot, "price wandered implausibly high"


def test_volatility_clusters_rather_than_being_constant():
    """ATR-scaled targets only differ from fixed ones if volatility varies."""
    atr = ind.atr(_df(6000)).dropna()
    assert atr.quantile(0.9) / atr.quantile(0.1) > 1.6


def test_sessions_differ_in_activity():
    df = _df(8000)
    df["hr"] = df["t"].str[11:13].astype(int)
    df["atr"] = ind.atr(df)
    asian = df[df.hr < 7]["atr"].median()
    overlap = df[(df.hr >= 13) & (df.hr < 16)]["atr"].median()
    assert overlap > asian * 1.2, "London/NY overlap should be livelier than Asian"


def test_candles_are_well_formed_and_chronological():
    df = _df(2000)
    assert (df["h"] >= df[["o", "c"]].max(axis=1)).all()
    assert (df["l"] <= df[["o", "c"]].min(axis=1)).all()
    ts = pd.to_datetime(df["t"], utc=True)
    assert ts.is_monotonic_increasing


def test_generation_is_deterministic_for_a_seed():
    assert gen_mod.generate(500, seed=7) == gen_mod.generate(500, seed=7)
    assert gen_mod.generate(500, seed=7) != gen_mod.generate(500, seed=8)


def test_finer_timeframes_scale_volatility_down():
    """M5 bars must be less volatile than M15 ones, or the MTF layer sees
    a lower timeframe that is noisier than its parent."""
    a15 = ind.atr(_df(3000, minutes=15)).dropna().median()
    a5 = ind.atr(_df(9000, minutes=5)).dropna().median()
    assert a5 < a15
