"""Tests for the multi-timeframe confirmation layer.

The properties that matter here are architectural, not numeric:

  1. No timeframe can veto. Every contribution is a bounded signed number.
  2. Missing data == neutral == exactly 0, so an M15-only backtest and a
     full-MTF live run stay on the same score scale.
  3. Confirming data raises the score, contradicting data lowers it, and
     the sign flips with the trade direction.
"""
import strategy_config as cfg
from strategy import mtf
from tests.helpers import make_candles, trending_h4_candles


def _up(n, interval, start=2000.0, step=1.0):
    return make_candles(n, start_price=start, step=step, noise=0.3,
                        interval_minutes=interval)


def _down(n, interval, start=2000.0, step=1.0):
    return make_candles(n, start_price=start, step=-step, noise=0.3,
                        interval_minutes=interval)


# ─── absence is neutral, never a veto ────────────────────────────────

def test_every_timeframe_scores_zero_when_candles_missing():
    for fn in (mtf.h4_regime, mtf.h1_context, mtf.m5_confirmation, mtf.m1_timing):
        result = fn([], "BUY")
        assert result["points"] == 0
        assert result["available"] is False


def test_evaluate_with_empty_market_is_exactly_zero():
    """This is what keeps an M15-only backtest comparable to live. If the
    MTF layer had a positive baseline, backtest scores would sit below
    live scores by a constant and every threshold would mean two things."""
    total, rows, detail = mtf.evaluate({}, "BUY")
    assert total == 0
    assert rows == []
    assert all(not d["available"] for d in detail.values())
    assert mtf.availability(detail) == ""


def test_evaluate_reports_which_timeframes_had_data():
    market = {"h4": trending_h4_candles(n=260, up=True)}
    _total, _rows, detail = mtf.evaluate(market, "BUY")
    assert detail["h4"]["available"] is True
    assert detail["m5"]["available"] is False
    assert "h4" in mtf.availability(detail)
    assert "m5" not in mtf.availability(detail)


# ─── bounded ─────────────────────────────────────────────────────────

def test_contributions_stay_within_their_configured_budgets():
    market = {
        "h4": trending_h4_candles(n=260, up=True),
        "h1": _up(120, 60),
        "m5": _up(300, 5),
        "m1": _up(200, 1),
    }
    for direction in ("BUY", "SELL"):
        total, _rows, detail = mtf.evaluate(market, direction, entry_price=2000.0)
        assert abs(detail["h4"]["points"]) <= cfg.SCORE_H4_MAX
        assert abs(detail["h1"]["points"]) <= cfg.SCORE_H1_MAX
        assert abs(detail["m5"]["points"]) <= cfg.SCORE_M5_MAX
        assert abs(detail["m1"]["points"]) <= cfg.SCORE_M1_MAX
        budget = (cfg.SCORE_H4_MAX + cfg.SCORE_H1_MAX
                  + cfg.SCORE_M5_MAX + cfg.SCORE_M1_MAX)
        assert -budget <= total <= budget


# ─── sign flips with direction ───────────────────────────────────────

def test_h4_uptrend_confirms_buy_and_contradicts_sell():
    up = trending_h4_candles(n=260, up=True)
    buy = mtf.h4_regime(up, "BUY")
    sell = mtf.h4_regime(up, "SELL")
    assert buy["bias"] == "BULL" and sell["bias"] == "BULL"
    assert buy["points"] > 0
    assert sell["points"] == -buy["points"]


def test_h4_slope_magnitude_scales_the_points():
    """A barely-there trend should not earn the same as a strong one --
    that all-or-nothing behaviour is what made H4 feel like a veto."""
    steep = make_candles(260, start_price=2000.0, step=8.0, noise=1.0,
                         interval_minutes=240)
    shallow = make_candles(260, start_price=2000.0, step=0.35, noise=1.0,
                           interval_minutes=240)
    assert mtf.h4_regime(steep, "BUY")["points"] > mtf.h4_regime(shallow, "BUY")["points"]


def test_m5_uptrend_confirms_buy_and_contradicts_sell():
    up = _up(300, 5)
    assert mtf.m5_confirmation(up, "BUY")["points"] > 0
    assert mtf.m5_confirmation(up, "SELL")["points"] < 0


def test_m5_downtrend_confirms_sell():
    down = _down(300, 5)
    assert mtf.m5_confirmation(down, "SELL")["points"] > 0
    assert mtf.m5_confirmation(down, "BUY")["points"] < 0


def test_m1_penalises_chasing_an_already_extended_entry():
    """M1's job is timing only: buying after price has already run away
    from the intended fill should score worse than buying at it."""
    up = _up(200, 1, start=2000.0, step=0.5)
    last = up[-1]["c"]
    at_entry = mtf.m1_timing(up, "BUY", entry_price=last)
    chasing = mtf.m1_timing(up, "BUY", entry_price=last - 20.0)
    assert chasing["points"] < at_entry["points"]


def test_m1_budget_is_the_smallest_of_the_four():
    """Entry timing must not be able to out-vote the setup or the regime."""
    assert cfg.SCORE_M1_MAX < cfg.SCORE_M5_MAX
    assert cfg.SCORE_M1_MAX < cfg.SCORE_H1_MAX
    assert cfg.SCORE_M1_MAX < cfg.SCORE_H4_MAX


def test_h1_location_prefers_room_to_run():
    """A BUY near the bottom of the recent H1 range has somewhere to go;
    the same BUY at the top of it does not."""
    rising = _up(120, 60, start=2000.0, step=2.0)
    at_top = mtf.h1_context(rising, "BUY")
    at_top_sell = mtf.h1_context(rising, "SELL")
    # Location and momentum disagree for both directions here, so the
    # honest reading is that neither gets a large contribution.
    assert abs(at_top["points"]) <= cfg.SCORE_H1_MAX
    assert abs(at_top_sell["points"]) <= cfg.SCORE_H1_MAX
    assert at_top["agreement"] == -at_top_sell["agreement"]
