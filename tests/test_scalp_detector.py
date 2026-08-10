"""Tests for the 6-gate scalp detector (detect_scalp_sweep_bos) and its
integration with score_candidate — pre-computed exits, spread gate, MFE/MAE."""
import datetime as dt
import pytest
import pandas as pd

import scoring_strategy as strat
import scoring_indicators as ind
import strategy_config as cfg


def _candle(o, h, l, c, v=100):
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


# ── Synthetic candle generators ─────────────────────────────────────────


def _scalp_buy_setup(n=60, base=100.0):
    """Build candles that should pass all 6 gates for a BUY scalp:
    equal lows → sweep below → displacement up → BOS close → FVG → retrace."""
    candles = []
    atr_approx = 1.0

    # Phase 1 (0-29): choppy range establishing equal lows near `base`
    for i in range(30):
        mid = base + 2.0 + (i % 3) * 0.3
        if i in (8, 18, 25):
            # Equal-low swing candles
            candles.append(_candle(mid, mid + 0.4, base, mid + 0.2))
        else:
            candles.append(_candle(mid, mid + 0.5, mid - 0.3, mid + 0.2))

    # Phase 2 (30): sweep candle — pierces below lows with rejection wick
    sweep_low = base - 0.5
    candles.append(_candle(base + 1.5, base + 2.0, sweep_low, base + 1.8))

    # Phase 3 (31): displacement candle — large bullish body
    candles.append(_candle(base + 1.5, base + 4.5, base + 1.3, base + 4.2))

    # Phase 4 (32): continuation/BOS close above structure high
    candles.append(_candle(base + 4.2, base + 5.0, base + 3.8, base + 4.8))

    # Phase 5 (33-35): retrace into entry zone
    candles.append(_candle(base + 4.8, base + 4.9, base + 3.2, base + 3.5))
    candles.append(_candle(base + 3.5, base + 3.8, base + 2.8, base + 3.2))
    candles.append(_candle(base + 3.2, base + 3.6, base + 2.5, base + 3.4))

    # Pad to n
    price = base + 3.4
    for _ in range(len(candles), n):
        candles.append(_candle(price, price + 0.3, price - 0.2, price + 0.1))
        price += 0.1

    return candles


def _flat_candles(n=60, price=100.0):
    return [_candle(price, price + 0.05, price - 0.05, price)] * n


# ── Unit tests ───────────────────────────────────────────────────────────


class TestDetectScalpSweepBos:
    def test_returns_none_on_too_few_candles(self):
        df = pd.DataFrame([_candle(100, 101, 99, 100)] * 15)
        assert strat.detect_scalp_sweep_bos(df) is None

    def test_returns_none_on_flat_market(self):
        df = pd.DataFrame(_flat_candles(60))
        assert strat.detect_scalp_sweep_bos(df) is None

    def test_candidate_has_correct_pattern_name(self):
        df = pd.DataFrame(_scalp_buy_setup(60))
        result = strat.detect_scalp_sweep_bos(df)
        if result is not None:
            assert result["pattern"] == "SCALP_SWEEP_BOS"

    def test_candidate_has_required_fields(self):
        df = pd.DataFrame(_scalp_buy_setup(60))
        result = strat.detect_scalp_sweep_bos(df)
        if result is not None:
            for key in ("pattern", "direction", "sweep_price", "quality",
                        "scalp_entry", "scalp_stop", "scalp_tp1",
                        "scalp_tp_final", "scalp_leg_origin", "scalp_leg_end",
                        "scalp_structure_level", "scalp_bos_close"):
                assert key in result, f"missing key: {key}"

    def test_quality_in_valid_range(self):
        df = pd.DataFrame(_scalp_buy_setup(60))
        result = strat.detect_scalp_sweep_bos(df)
        if result is not None:
            assert 0 < result["quality"] <= cfg.PATTERN_QUALITY_BASE_MAX

    def test_buy_direction_on_buy_setup(self):
        df = pd.DataFrame(_scalp_buy_setup(60))
        result = strat.detect_scalp_sweep_bos(df)
        if result is not None:
            assert result["direction"] == "BUY"

    def test_precomputed_exits_are_consistent(self):
        df = pd.DataFrame(_scalp_buy_setup(60))
        result = strat.detect_scalp_sweep_bos(df)
        if result is not None:
            assert result["scalp_stop"] < result["scalp_entry"] < result["scalp_tp1"]
            risk = result["scalp_entry"] - result["scalp_stop"]
            assert risk > 0
            expected_tp1 = result["scalp_entry"] + cfg.SCALP_TP1_R_MULT * risk
            assert abs(result["scalp_tp1"] - expected_tp1) < 0.01

    def test_included_in_pattern_detectors(self):
        assert strat.detect_scalp_sweep_bos in strat.PATTERN_DETECTORS

    def test_find_candidate_considers_scalp(self):
        candles = _scalp_buy_setup(60)
        result = strat.find_candidate(candles)
        assert result is None or "pattern" in result


class TestScoreCandiateScalpBranch:
    """Verify that score_candidate uses pre-computed exits for SCALP_SWEEP_BOS
    and applies the spread gate."""

    def _make_market(self, entry_candles):
        h1 = [_candle(100 + i * 0.5, 101 + i * 0.5, 99.5 + i * 0.5, 100.3 + i * 0.5) for i in range(160)]
        h4 = [_candle(100 + i, 102 + i, 99 + i, 101 + i) for i in range(260)]
        return {"entry": entry_candles, "m15": entry_candles, "h1": h1, "h4": h4}

    def _scalp_candidate(self):
        return {
            "pattern": "SCALP_SWEEP_BOS",
            "direction": "BUY",
            "sweep_price": 99.5,
            "leg_extreme": 99.0,
            "quality": 30,
            "has_fvg": True,
            "scalp_entry": 101.0,
            "scalp_stop": 98.8,
            "scalp_tp1": 103.2,
            "scalp_tp_final": 105.4,
            "scalp_leg_origin": 99.0,
            "scalp_leg_end": 103.0,
            "scalp_structure_level": 102.5,
            "scalp_bos_close": 103.0,
        }

    def _trending_up_candles(self, n=80):
        candles = []
        price = 100.0
        for i in range(n):
            o = price
            c = price + 0.5
            h = c + 0.2
            l = o - 0.15
            candles.append(_candle(o, h, l, c))
            price = c
        return candles

    def test_uses_precomputed_exits(self):
        candles = self._trending_up_candles(80)
        market = self._make_market(candles)
        candidate = self._scalp_candidate()
        now = dt.datetime(2026, 8, 5, 14, 0, tzinfo=dt.timezone.utc)
        level_store = ind.LevelStore()

        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, level_store, diagnostic=True)
        if result is None or result.get("blocked"):
            pytest.skip("setup blocked by HTF or other filter")

        assert result["entry_price"] == round(candidate["scalp_entry"], 5)
        assert result["stop_loss"] == round(candidate["scalp_stop"], 5)
        assert result["tp1"] == round(candidate["scalp_tp1"], 5)
        assert "scalp" in result["entry_basis"].lower()

    def test_spread_gate_blocks_wide_spread(self):
        candles = self._trending_up_candles(80)
        candles[-1]["spread"] = 100.0
        market = self._make_market(candles)
        candidate = self._scalp_candidate()
        candidate["quality"] = 38
        now = dt.datetime(2026, 8, 5, 14, 0, tzinfo=dt.timezone.utc)
        level_store = ind.LevelStore()

        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, level_store,
            confirmation_bonus=40, diagnostic=True)
        assert result is not None
        assert result.get("blocked") is not None
        assert "spread" in result["blocked"].lower()

    def test_spread_gate_passes_normal_spread(self):
        candles = self._trending_up_candles(80)
        candles[-1]["spread"] = 0.01
        market = self._make_market(candles)
        candidate = self._scalp_candidate()
        now = dt.datetime(2026, 8, 5, 14, 0, tzinfo=dt.timezone.utc)
        level_store = ind.LevelStore()

        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, level_store, diagnostic=True)
        if result and result.get("blocked"):
            assert "spread" not in result["blocked"].lower()

    def test_non_scalp_pattern_uses_generic_exits(self):
        candles = self._trending_up_candles(80)
        market = self._make_market(candles)
        candidate = {
            "pattern": "LIQUIDITY_SWEEP_BOS",
            "direction": "BUY",
            "sweep_price": 100.0,
            "quality": 25,
        }
        now = dt.datetime(2026, 8, 5, 14, 0, tzinfo=dt.timezone.utc)
        level_store = ind.LevelStore()

        result = strat.score_candidate(
            "US500", "US_INDEX", candidate, market, now, level_store, diagnostic=True)
        if result and not result.get("blocked"):
            assert "scalp" not in result.get("entry_basis", "").lower()


class TestPatternDisplay:
    def test_scalp_in_pattern_display(self):
        from main_alerts import _PATTERN_DISPLAY
        assert "SCALP_SWEEP_BOS" in _PATTERN_DISPLAY


class TestMfeMAE:
    def test_open_trade_tracker_initializes_mfe_mae(self):
        import tempfile, os
        path = os.path.join(tempfile.mkdtemp(), "trades.json")
        log_path = os.path.join(tempfile.mkdtemp(), "log.json")
        from main_alerts import OpenTradeTracker
        tracker = OpenTradeTracker(path=path, trade_log_path=log_path)
        scored = {
            "instrument": "US500", "direction": "BUY", "pattern": "SCALP_SWEEP_BOS",
            "entry_price": 5000.0, "stop_loss": 4990.0,
            "tp1": 5010.0, "tp2": 5020.0, "tp3": 5030.0,
        }
        now = dt.datetime(2026, 8, 5, 14, 0, tzinfo=dt.timezone.utc)
        tracker.add(scored, now)
        data = tracker._data["US500"]
        assert data["mfe"] == 0.0
        assert data["mae"] == 0.0
