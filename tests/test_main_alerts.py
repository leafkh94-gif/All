import datetime as dt

import main_alerts as ma
from strategy import modes
from strategy import scan_diagnostics


def test_hard_flat_active_after_1830_us_index():
    t = dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "US500") is True


def test_hard_flat_inactive_before_1830_us_index():
    t = dt.datetime(2026, 7, 1, 18, 29, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "US500") is False


def test_hard_flat_applies_to_crypto_too():
    """v1.3: session discipline now applies to BTCUSD too -- it's no longer
    exempt just because it has no session structure for liquidity levels."""
    t = dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "BTCUSD") is True


def test_hard_flat_applies_to_eurusd():
    t = dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "EURUSD") is True


def test_hard_flat_applies_to_gbpjpy():
    t = dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "GBPJPY") is True


def test_hard_flat_disabled_for_swing_mode_even_past_1830():
    t = dt.datetime(2026, 7, 1, 20, 0, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "US500", mode=modes.SWING) is False


def test_hard_flat_still_applies_for_standard_mode_explicitly_passed():
    t = dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "US500", mode=modes.STANDARD) is True


def test_dedup_us_index_keeps_best_score_same_direction():
    candidates = [
        ("US500", {"direction": "BUY", "score": 80}),
        ("US100", {"direction": "BUY", "score": 90}),
        ("US30", {"direction": "SELL", "score": 70}),
        ("BTCUSD", {"direction": "BUY", "score": 60}),
    ]
    kept = ma.dedup_us_index_candidates(candidates)
    kept_dict = dict(kept)
    assert "US100" in kept_dict and "US500" not in kept_dict
    assert "US30" in kept_dict   # different direction, not deduped away
    assert "BTCUSD" in kept_dict  # BTC always exempt/kept


def test_active_entry_tracker_touch_removes_without_message(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.ActiveEntryTracker(path=str(tmp_path / "entries.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 4999.0  # price traded down through the BUY limit entry

    tracker.evaluate_all(now + dt.timedelta(minutes=15), FakeFeed())
    assert sent == []
    assert "US500" not in tracker._data


def test_r_multiple_buy_and_sell_directions():
    assert ma._r_multiple("BUY", 100.0, 10.0, 120.0) == 2.0
    assert ma._r_multiple("SELL", 100.0, 10.0, 80.0) == 2.0
    assert ma._r_multiple("BUY", 100.0, 10.0, 90.0) == -1.0


def test_r_multiple_zero_risk_returns_zero():
    assert ma._r_multiple("BUY", 100.0, 0.0, 150.0) == 0.0


def test_append_trade_log_caps_length(tmp_path):
    path = str(tmp_path / "trade_log.json")
    for i in range(ma.TRADE_LOG_MAX_ENTRIES + 10):
        ma._append_trade_log({"instrument": "US500", "r_multiple": i}, path=path)
    entries = ma.load_json(path)["entries"]
    assert len(entries) == ma.TRADE_LOG_MAX_ENTRIES
    assert entries[-1]["r_multiple"] == ma.TRADE_LOG_MAX_ENTRIES + 9  # newest kept


def test_active_entry_tracker_touch_hands_off_to_open_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(ma, "send_telegram", lambda text: None)
    tracker = ma.ActiveEntryTracker(path=str(tmp_path / "entries.json"))
    open_tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "pattern": "SD_REJECTION"}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 4999.0  # traded down through the BUY limit entry -> filled

    tracker.evaluate_all(now + dt.timedelta(minutes=15), FakeFeed(), open_tracker=open_tracker)
    assert "US500" not in tracker._data
    assert "US500" in open_tracker._data
    assert open_tracker._data["US500"]["stop_loss"] == 4980.0
    assert open_tracker._data["US500"]["tp1_hit"] is False
    assert open_tracker._data["US500"]["pattern"] == "SD_REJECTION"
    assert open_tracker._data["US500"]["initial_risk"] == 20.0


def test_open_trade_tracker_tp1_hit_closes_half_and_moves_stop_to_breakeven(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "tp3": 5080.0}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5041.0  # price reached TP1

    tracker.evaluate_all(now + dt.timedelta(minutes=15), FakeFeed())
    assert any("TP1 hit" in m for m in sent)
    assert tracker._data["US500"]["tp1_hit"] is True
    assert tracker._data["US500"]["stop_loss"] == 5000.0  # moved to breakeven
    assert tracker._data["US500"]["locked_r"] == 1.0  # 0.5 * (40/20)
    assert ma.load_json(tracker.trade_log_path).get("entries", []) == []  # not a final close yet


def test_open_trade_tracker_stop_hit_before_tp1_closes_full_position(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "tp3": 5080.0, "pattern": "FLAG"}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 4979.0  # price hit the original stop before TP1

    tracker.evaluate_all(now + dt.timedelta(minutes=15), FakeFeed())
    assert any("stop loss hit" in m for m in sent)
    assert "US500" not in tracker._data
    entry = ma.load_json(tracker.trade_log_path)["entries"][0]
    assert entry["outcome"] == "stop_before_tp1"
    assert entry["pattern"] == "FLAG"
    assert entry["r_multiple"] == -1.0


def test_open_trade_tracker_tp2_hit_moves_to_runner_phase_not_a_final_close(tmp_path, monkeypatch):
    """v1.3 §5: TP2 closes 30% and moves SL to TP1 -- the remaining 20%
    becomes a runner targeting TP3, it does NOT close the trade."""
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "tp3": 5080.0}, now)
    tracker._data["US500"]["tp1_hit"] = True
    tracker._data["US500"]["stop_loss"] = 5000.0  # already at breakeven
    tracker._data["US500"]["locked_r"] = 1.0  # as if TP1 had already fired at 5040

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5061.0  # price reached TP2

    tracker.evaluate_all(now + dt.timedelta(minutes=30), FakeFeed())
    assert any("TP2 hit" in m for m in sent)
    assert "US500" in tracker._data  # still open -- runner phase, not closed
    assert tracker._data["US500"]["tp2_hit"] is True
    assert tracker._data["US500"]["stop_loss"] == 5040.0  # moved to TP1
    assert tracker._data["US500"]["locked_r"] == 1.9  # locked 1.0 + 0.3 * (60/20)
    assert ma.load_json(tracker.trade_log_path).get("entries", []) == []  # not a final close yet


def test_open_trade_tracker_tp3_hit_after_tp2_closes_runner(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "tp3": 5080.0}, now)
    tracker._data["US500"]["tp1_hit"] = True
    tracker._data["US500"]["tp2_hit"] = True
    tracker._data["US500"]["stop_loss"] = 5040.0  # at TP1, as if TP2 already fired
    tracker._data["US500"]["locked_r"] = 2.5

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5081.0  # price reached TP3

    tracker.evaluate_all(now + dt.timedelta(minutes=45), FakeFeed())
    assert any("TP3 hit" in m for m in sent)
    assert "US500" not in tracker._data
    entry = ma.load_json(tracker.trade_log_path)["entries"][0]
    assert entry["outcome"] == "tp3_runner_complete"
    assert entry["r_multiple"] == 3.3  # locked 2.5 + 0.2 * (80/20)


def test_open_trade_tracker_runner_stopped_after_tp2(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "tp3": 5080.0}, now)
    tracker._data["US500"]["tp1_hit"] = True
    tracker._data["US500"]["tp2_hit"] = True
    tracker._data["US500"]["stop_loss"] = 5040.0  # at TP1
    tracker._data["US500"]["locked_r"] = 2.5

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5039.0  # pulled back to the TP1-level runner stop

    tracker.evaluate_all(now + dt.timedelta(minutes=45), FakeFeed())
    assert any("runner stopped" in m for m in sent)
    assert "US500" not in tracker._data
    entry = ma.load_json(tracker.trade_log_path)["entries"][0]
    assert entry["outcome"] == "runner_stopped"
    assert entry["r_multiple"] == 2.9  # locked 2.5 + 0.2 * (40/20) at the TP1-level stop


def test_open_trade_tracker_breakeven_stop_after_tp1_closes_remainder(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "tp3": 5080.0}, now)
    tracker._data["US500"]["tp1_hit"] = True
    tracker._data["US500"]["stop_loss"] = 5000.0  # breakeven
    tracker._data["US500"]["locked_r"] = 1.0  # as if TP1 had already fired at 5040

    class FakeFeed:
        def get_current_price(self, instrument):
            return 4999.0  # pulled back to breakeven stop after TP1

    tracker.evaluate_all(now + dt.timedelta(minutes=30), FakeFeed())
    assert any("breakeven stop hit" in m for m in sent)
    assert "US500" not in tracker._data
    entry = ma.load_json(tracker.trade_log_path)["entries"][0]
    assert entry["outcome"] == "breakeven_after_tp1"
    assert entry["r_multiple"] == 1.0  # locked 1.0 + 0.5 * 0


def test_open_trade_tracker_session_cutoff_closes_every_instrument_including_btc(tmp_path, monkeypatch):
    """v1.3: session discipline now applies to BTCUSD too -- it's no longer
    exempt from the 18:30 UTC hard flat."""
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "tp3": 5080.0}, now)
    tracker.add({"instrument": "BTCUSD", "direction": "BUY", "entry_price": 60000.0,
                 "stop_loss": 59000.0, "tp1": 61000.0, "tp2": 62000.0, "tp3": 63000.0}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5010.0 if instrument == "US500" else 60100.0  # neither TP/stop touched

    past_hard_flat = dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.timezone.utc)
    tracker.evaluate_all(past_hard_flat, FakeFeed())
    assert any("US500" in m and "hard flat" in m for m in sent)
    assert any("BTCUSD" in m and "hard flat" in m for m in sent)
    assert "US500" not in tracker._data
    assert "BTCUSD" not in tracker._data


def test_open_trade_tracker_swing_mode_holds_through_session_cutoff(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "tp3": 5080.0}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5010.0  # neither TP/stop touched

    past_hard_flat = dt.datetime(2026, 7, 1, 20, 0, tzinfo=dt.timezone.utc)
    tracker.evaluate_all(past_hard_flat, FakeFeed(), mode=modes.SWING)
    assert sent == []
    assert "US500" in tracker._data  # swing mode intentionally holds past the day-trade cutoff


def test_open_trade_tracker_session_cutoff_after_tp1_blends_locked_r(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"), trade_log_path=str(tmp_path / "trade_log.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0, "tp3": 5080.0}, now)
    tracker._data["US500"]["tp1_hit"] = True
    tracker._data["US500"]["stop_loss"] = 5000.0
    tracker._data["US500"]["locked_r"] = 1.0

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5030.0  # short of TP2, above breakeven, when cutoff hits

    past_hard_flat = dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.timezone.utc)
    tracker.evaluate_all(past_hard_flat, FakeFeed())
    entry = ma.load_json(tracker.trade_log_path)["entries"][0]
    assert entry["outcome"] == "session_cutoff_after_tp1"
    assert entry["r_multiple"] == 1.75  # locked 1.0 + 0.5 * (30/20)
    assert "US500" not in tracker._data


def test_active_entry_tracker_expires_after_2_hours(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.ActiveEntryTracker(path=str(tmp_path / "entries.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5050.0  # never touched the entry

    tracker.evaluate_all(now + dt.timedelta(hours=2, minutes=1), FakeFeed())
    assert any("entry expired" in m for m in sent)
    assert "US500" not in tracker._data


def test_active_entry_tracker_expiry_is_flat_90_minutes_regardless_of_mode(monkeypatch):
    """v1.3 Section 2: the pending-order EXPIRED timer is a flat 90 minutes
    (6 x M15 bars) across every mode -- not mode/instrument-scaled like the
    old system."""
    monkeypatch.setattr(ma, "send_telegram", lambda text: None)
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5050.0  # never touched the entry

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        default_tracker = ma.ActiveEntryTracker(path=f"{tmp}/entries_default.json")
        default_tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0}, now)
        default_tracker.evaluate_all(now + dt.timedelta(minutes=89), FakeFeed())
        assert "US500" in default_tracker._data  # 90-min expiry not yet reached

        fast_tracker = ma.ActiveEntryTracker(path=f"{tmp}/entries_fast.json")
        fast_tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0}, now)
        fast_tracker.evaluate_all(now + dt.timedelta(minutes=89), FakeFeed(), mode=modes.FAST)
        assert "US500" in fast_tracker._data  # same flat 90-min expiry under fast mode too

        default_tracker.evaluate_all(now + dt.timedelta(minutes=91), FakeFeed())
        assert "US500" not in default_tracker._data


def test_build_market_fast_mode_requests_5min_entry():
    requested = {}

    class FakeFeed:
        def get_candles(self, instrument, interval, n=60):
            requested[interval] = requested.get(interval, 0) + 1
            return []

    ma.build_market(FakeFeed(), "US500", mode=modes.FAST)
    assert "5min" in requested
    assert "15min" not in requested


def test_build_market_defaults_to_15min_entry():
    requested = {}

    class FakeFeed:
        def get_candles(self, instrument, interval, n=60):
            requested[interval] = requested.get(interval, 0) + 1
            return []

    ma.build_market(FakeFeed(), "US500")
    assert "15min" in requested


def test_load_active_mode_defaults_to_standard_with_no_state_file(tmp_path):
    mode = ma.load_active_mode(path=str(tmp_path / "mode.json"))
    assert mode is modes.STANDARD


def test_save_and_load_active_mode_roundtrip(tmp_path):
    path = str(tmp_path / "mode.json")
    ma.save_active_mode_name("fast", path=path)
    assert ma.load_active_mode(path=path) is modes.FAST


def test_load_active_mode_falls_back_on_invalid_name(tmp_path):
    path = str(tmp_path / "mode.json")
    ma.save_json(path, {"mode": "not-a-real-mode"})
    assert ma.load_active_mode(path=path) is modes.STANDARD


def test_format_aplus_alert_contains_partial_tp_guidance():
    scored = {
        "instrument": "US500", "direction": "BUY", "entry_price": 5420.0,
        "stop_loss": 5398.0, "tp1": 5464.0, "tp2": 5508.0, "tp3": 5552.0,
        "score": 82, "htf_bias": "TRENDING_UP",
        "breakdown": {"pattern": "LIQUIDITY_SWEEP_BOS", "pdh_pdl": "PDH"},
    }
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    body = ma.format_aplus_alert(scored, now)
    assert "A+ SIGNAL — US500" in body
    assert "SL to breakeven" in body
    assert "5552.0" in body  # TP3 shown
    assert "18:30" in body


def test_format_aplus_alert_adds_correlation_tag_for_cluster_member():
    scored = {
        "instrument": "AUDJPY", "direction": "BUY", "entry_price": 100.0,
        "stop_loss": 99.0, "tp1": 101.0, "tp2": 102.0, "tp3": 103.0,
        "score": 82, "htf_bias": "TRENDING_UP",
        "breakdown": {"pattern": "LIQUIDITY_SWEEP_BOS"},
    }
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    body = ma.format_aplus_alert(scored, now)
    assert "Correlated cluster" in body


def test_format_aplus_alert_no_correlation_tag_outside_cluster():
    scored = {
        "instrument": "US500", "direction": "BUY", "entry_price": 5420.0,
        "stop_loss": 5398.0, "tp1": 5464.0, "tp2": 5508.0, "tp3": 5552.0,
        "score": 82, "htf_bias": "TRENDING_UP",
        "breakdown": {"pattern": "LIQUIDITY_SWEEP_BOS"},
    }
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    body = ma.format_aplus_alert(scored, now)
    assert "Correlated cluster" not in body


def test_format_watch_alert_adds_correlation_tag_for_cluster_member():
    scored = {"instrument": "JP225", "direction": "SELL", "entry_price": 38000.0, "score": 65}
    expires = dt.datetime(2026, 7, 1, 16, 0, tzinfo=dt.timezone.utc)
    body = ma.format_watch_alert(scored, expires)
    assert "Correlated cluster" in body


def test_format_watch_alert_no_correlation_tag_for_hk50():
    # HK50/A50 are flagged for diagnostics purposes, not part of the FX
    # risk-on/off correlation cluster.
    scored = {"instrument": "HK50", "direction": "SELL", "entry_price": 18000.0, "score": 65}
    expires = dt.datetime(2026, 7, 1, 16, 0, tzinfo=dt.timezone.utc)
    body = ma.format_watch_alert(scored, expires)
    assert "Correlated cluster" not in body


def test_daily_reset_if_needed_resets_new_day():
    state = {"aplus_count_date": "2026-06-30", "aplus_count": 5}
    ma.daily_reset_if_needed(state, dt.datetime(2026, 7, 1, 0, 0, tzinfo=dt.timezone.utc))
    assert state["aplus_count"] == 0
    assert state["aplus_count_date"] == "2026-07-01"


def test_daily_reset_if_needed_zeroes_daily_loss_on_new_day():
    state = {"aplus_count_date": "2026-06-30", "aplus_count": 5, "daily_loss_total": 20.0}
    ma.daily_reset_if_needed(state, dt.datetime(2026, 7, 1, 0, 0, tzinfo=dt.timezone.utc))
    assert state["daily_loss_total"] == 0.0


def test_record_loss_accumulates_and_trips_breaker_at_limit(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    path = str(tmp_path / "main_state.json")
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    total = ma.record_loss(15.0, now_utc=now, path=path)
    assert total == 15.0
    assert sent == []  # under the $20 limit — no notification yet
    total = ma.record_loss(5.0, now_utc=now, path=path)
    assert total == 20.0
    assert any("Daily loss limit" in m for m in sent)


def test_record_loss_only_notifies_once_when_already_tripped(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    path = str(tmp_path / "main_state.json")
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    ma.record_loss(20.0, now_utc=now, path=path)
    assert len(sent) == 1
    total = ma.record_loss(5.0, now_utc=now, path=path)
    assert total == 25.0
    assert len(sent) == 1  # no second notification


def test_record_win_reduces_daily_total_and_can_go_negative(tmp_path):
    path = str(tmp_path / "main_state.json")
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    ma.record_loss(10.0, now_utc=now, path=path)
    total = ma.record_win(15.0, now_utc=now, path=path)
    assert total == -5.0


def test_weekly_performance_report_text_reports_no_trades():
    now = dt.datetime(2026, 7, 3, 21, 0, tzinfo=dt.timezone.utc)  # a Friday
    text = ma.weekly_performance_report_text([], now)
    assert "no trades closed this week" in text


def test_weekly_performance_report_text_excludes_trades_older_than_a_week():
    now = dt.datetime(2026, 7, 3, 21, 0, tzinfo=dt.timezone.utc)
    entries = [
        {"pattern": "FLAG", "r_multiple": 1.5, "closed_at": (now - dt.timedelta(days=2)).isoformat()},
        {"pattern": "FLAG", "r_multiple": -1.0, "closed_at": (now - dt.timedelta(days=10)).isoformat()},
    ]
    text = ma.weekly_performance_report_text(entries, now)
    assert "Trades closed: 1" in text
    assert "+1.50R avg" in text


def test_weekly_performance_report_text_groups_by_pattern():
    now = dt.datetime(2026, 7, 3, 21, 0, tzinfo=dt.timezone.utc)
    entries = [
        {"pattern": "FLAG", "r_multiple": 2.0, "closed_at": (now - dt.timedelta(hours=1)).isoformat()},
        {"pattern": "FLAG", "r_multiple": -1.0, "closed_at": (now - dt.timedelta(hours=2)).isoformat()},
        {"pattern": "HEAD_SHOULDERS", "r_multiple": 1.0, "closed_at": (now - dt.timedelta(hours=3)).isoformat()},
    ]
    text = ma.weekly_performance_report_text(entries, now)
    assert "Trades closed: 3" in text
    assert "FLAG: 2 trades" in text
    assert "HEAD_SHOULDERS: 1 trades" in text


def test_weekly_performance_report_text_never_raises_on_malformed_entry():
    now = dt.datetime(2026, 7, 3, 21, 0, tzinfo=dt.timezone.utc)
    entries = [{"pattern": "FLAG"}, {"closed_at": "not-a-date", "r_multiple": 1.0}, {}]
    text = ma.weekly_performance_report_text(entries, now)
    assert "no trades closed this week" in text


def test_maybe_send_weekly_performance_report_only_fires_friday_2100(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    path = str(tmp_path / "trade_log.json")
    ma.save_json(path, {"entries": [
        {"pattern": "FLAG", "r_multiple": 1.0, "closed_at": "2026-07-03T20:00:00+00:00"}]})
    state = {}
    ma.maybe_send_weekly_performance_report(
        state, dt.datetime(2026, 7, 3, 20, 0, tzinfo=dt.timezone.utc), path=path)  # Friday, wrong hour
    assert sent == []
    ma.maybe_send_weekly_performance_report(
        state, dt.datetime(2026, 7, 3, 21, 0, tzinfo=dt.timezone.utc), path=path)  # Friday 21:00 UTC
    assert len(sent) == 1
    assert state["last_weekly_report_week"] == "2026-W27"


def test_maybe_send_weekly_performance_report_fires_once_per_week(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    path = str(tmp_path / "trade_log.json")
    ma.save_json(path, {"entries": []})
    state = {}
    now = dt.datetime(2026, 7, 3, 21, 0, tzinfo=dt.timezone.utc)
    ma.maybe_send_weekly_performance_report(state, now, path=path)
    ma.maybe_send_weekly_performance_report(state, now, path=path)  # same week -- no second send
    assert len(sent) == 1


def test_ensure_loss_breaker_window_sets_start_only_once():
    state = {}
    first = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    ma.ensure_loss_breaker_window(state, first)
    first_until = state["loss_breaker_active_until"]
    later = dt.datetime(2026, 7, 5, 10, 0, tzinfo=dt.timezone.utc)
    ma.ensure_loss_breaker_window(state, later)
    assert state["loss_breaker_active_until"] == first_until  # not reset on a later touch


def test_loss_breaker_window_active_within_14_days_inactive_after():
    state = {}
    start = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    ma.ensure_loss_breaker_window(state, start)
    within = start + dt.timedelta(days=13)
    after = start + dt.timedelta(days=15)
    assert ma.loss_breaker_window_active(state, within) is True
    assert ma.loss_breaker_window_active(state, after) is False


def test_record_loss_does_not_trip_or_notify_after_trial_window_ends(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    path = str(tmp_path / "main_state.json")
    start = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    ma.record_loss(1.0, now_utc=start, path=path)  # starts the trial window

    after_trial = start + dt.timedelta(days=15)
    # Seed a same-day total already past the limit, isolating the trial-expiry
    # check from the unrelated daily reset (a new UTC day would zero it anyway).
    state = ma.load_json(path)
    state["aplus_count_date"] = after_trial.strftime("%Y-%m-%d")
    state["daily_loss_total"] = 25.0
    ma.save_json(path, state)

    total = ma.record_loss(5.0, now_utc=after_trial, path=path)
    assert total == 30.0
    assert sent == []  # no breaker-tripped notification once the trial has expired


def test_set_blackout_makes_manual_blackout_active(tmp_path):
    path = str(tmp_path / "main_state.json")
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    ma.set_blackout(30, now_utc=now, path=path)
    state = ma.load_json(path)
    assert ma.manual_blackout_active(state, now + dt.timedelta(minutes=10)) is True
    assert ma.manual_blackout_active(state, now + dt.timedelta(minutes=31)) is False


def test_clear_blackout_ends_it_early(tmp_path):
    path = str(tmp_path / "main_state.json")
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    ma.set_blackout(30, now_utc=now, path=path)
    ma.clear_blackout(path=path)
    state = ma.load_json(path)
    assert ma.manual_blackout_active(state, now + dt.timedelta(minutes=5)) is False


def test_manual_blackout_active_false_with_no_state():
    assert ma.manual_blackout_active({}, dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)) is False

def test_no_pattern_blocked_message_includes_bars_diagnostic():
    """Reproduces the exact blocked-message construction from main_alerts.run()'s
    per-instrument scan loop when find_candidate returns None, to confirm the
    bar-count detail (data problem vs detectors-too-tight) is folded in."""
    too_few_candles = [{"t": f"2026-01-01T00:{i:02d}:00", "o": 1, "h": 2, "l": 0, "c": 1}
                        for i in range(10)]
    now = dt.datetime(2026, 1, 1, 0, 20, tzinfo=dt.timezone.utc)
    bars_diag = scan_diagnostics.bars_report("US500", too_few_candles, now)
    blocked = f"no pattern detected ({bars_diag.split(': ', 1)[1]})"
    assert "no pattern detected" in blocked
    assert "10/30 bars" in blocked
    assert "data problem" in blocked
