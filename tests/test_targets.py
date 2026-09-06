"""Tests for the shared SL/TP ladder.

Both detectors must build targets identically, and the ATR mode must
actually adapt to volatility -- that adaptivity is the whole reason it
exists as a testable alternative to the fixed $25 ladder.
"""
import strategy_config as cfg
from strategy import targets
from tests.helpers import make_candles


def _atr_candles(n=100, noise=0.0, step=0.0):
    return make_candles(n, start_price=2000.0, step=step, noise=noise)


# ─── FIXED mode ──────────────────────────────────────────────────────

def test_fixed_mode_uses_configured_dollar_distances():
    t = targets.build_targets(2000.0, "BUY", atr_value=3.0, mode="FIXED")
    pt = cfg.POINT_VALUE
    assert t["risk"] == cfg.FIXED_SL_POINTS * pt
    assert t["stop_loss"] == 2000.0 - cfg.FIXED_SL_POINTS * pt
    assert t["tp1"] == 2000.0 + cfg.FIXED_TP1_POINTS * pt
    assert t["tp2"] == 2000.0 + cfg.FIXED_TP2_POINTS * pt
    assert t["tp3"] == 2000.0 + cfg.FIXED_TP3_POINTS * pt


def test_fixed_mode_ignores_atr_entirely():
    """This is precisely the property under review: the fixed ladder uses
    the same distance in a dead range and a news spike."""
    quiet = targets.build_targets(2000.0, "BUY", atr_value=0.5, mode="FIXED")
    wild = targets.build_targets(2000.0, "BUY", atr_value=12.0, mode="FIXED")
    assert quiet["risk"] == wild["risk"]


def test_sell_targets_mirror_buy_targets():
    buy = targets.build_targets(2000.0, "BUY", atr_value=3.0, mode="FIXED")
    sell = targets.build_targets(2000.0, "SELL", atr_value=3.0, mode="FIXED")
    assert buy["stop_loss"] - 2000.0 == -(sell["stop_loss"] - 2000.0)
    assert buy["tp3"] - 2000.0 == -(sell["tp3"] - 2000.0)
    assert buy["risk"] == sell["risk"]


# ─── ATR mode ────────────────────────────────────────────────────────

def test_atr_mode_widens_the_stop_in_a_volatile_regime():
    quiet = targets.build_targets(2000.0, "BUY", atr_value=2.0, mode="ATR")
    wild = targets.build_targets(2000.0, "BUY", atr_value=5.0, mode="ATR")
    assert wild["risk"] > quiet["risk"]


def test_atr_mode_clamps_the_stop_at_both_ends():
    tiny = targets.build_targets(2000.0, "BUY", atr_value=0.01, mode="ATR")
    huge = targets.build_targets(2000.0, "BUY", atr_value=500.0, mode="ATR")
    assert tiny["risk"] == cfg.ATR_SL_MIN_POINTS * cfg.POINT_VALUE
    assert huge["risk"] == cfg.ATR_SL_MAX_POINTS * cfg.POINT_VALUE


def test_atr_mode_keeps_the_r_multiple_ladder():
    t = targets.build_targets(2000.0, "BUY", atr_value=3.0, mode="ATR")
    r = t["risk"]
    assert abs((t["tp1"] - 2000.0) / r - cfg.ATR_TP1_R) < 1e-9
    assert abs((t["tp2"] - 2000.0) / r - cfg.ATR_TP2_R) < 1e-9
    assert abs((t["tp3"] - 2000.0) / r - cfg.ATR_TP3_R) < 1e-9


def test_atr_mode_falls_back_to_fixed_when_atr_unavailable():
    """Better to use the known distance than to invent one from nothing."""
    t = targets.build_targets(2000.0, "BUY", atr_value=None, mode="ATR")
    assert t["risk"] == cfg.FIXED_SL_POINTS * cfg.POINT_VALUE


def test_atr_from_candles_returns_none_on_insufficient_history():
    assert targets.atr_from_candles(make_candles(3)) is None
    assert targets.atr_from_candles(None) is None


def test_atr_from_candles_reads_a_positive_value_from_real_movement():
    v = targets.atr_from_candles(_atr_candles(n=100, noise=1.5, step=0.5))
    assert v is not None and v > 0
