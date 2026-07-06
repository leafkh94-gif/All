import datetime as dt

import main_alerts as ma
from strategy import modes
from strategy import scan_diagnostics


def test_hard_flat_active_after_1830_us_index():
    t = dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "US_INDEX") is True


def test_hard_flat_inactive_before_1830_us_index():
    t = dt.datetime(2026, 7, 1, 18, 29, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "US_INDEX") is False


def test_hard_flat_never_applies_to_crypto():
    t = dt.datetime(2026, 7, 1, 23, 0, tzinfo=dt.timezone.utc)
    assert ma.hard_flat_active(t, "CRYPTO") is False


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


def test_active_entry_tracker_touch_hands_off_to_open_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(ma, "send_telegram", lambda text: None)
    tracker = ma.ActiveEntryTracker(path=str(tmp_path / "entries.json"))
    open_tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 4999.0  # traded down through the BUY limit entry -> filled

    tracker.evaluate_all(now + dt.timedelta(minutes=15), FakeFeed(), open_tracker=open_tracker)
    assert "US500" not in tracker._data
    assert "US500" in open_tracker._data
    assert open_tracker._data["US500"]["stop_loss"] == 4980.0
    assert open_tracker._data["US500"]["tp1_hit"] is False


def test_open_trade_tracker_tp1_hit_closes_half_and_moves_stop_to_breakeven(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5041.0  # price reached TP1

    tracker.evaluate_all(now + dt.timedelta(minutes=15), FakeFeed())
    assert any("TP1 hit" in m for m in sent)
    assert tracker._data["US500"]["tp1_hit"] is True
    assert tracker._data["US500"]["stop_loss"] == 5000.0  # moved to breakeven


def test_open_trade_tracker_stop_hit_before_tp1_closes_full_position(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 4979.0  # price hit the original stop before TP1

    tracker.evaluate_all(now + dt.timedelta(minutes=15), FakeFeed())
    assert any("stop loss hit" in m for m in sent)
    assert "US500" not in tracker._data


def test_open_trade_tracker_tp2_hit_after_tp1_closes_trade(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0}, now)
    tracker._data["US500"]["tp1_hit"] = True
    tracker._data["US500"]["stop_loss"] = 5000.0  # already at breakeven

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5061.0  # price reached TP2

    tracker.evaluate_all(now + dt.timedelta(minutes=30), FakeFeed())
    assert any("TP2 hit" in m for m in sent)
    assert "US500" not in tracker._data


def test_open_trade_tracker_breakeven_stop_after_tp1_closes_remainder(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0}, now)
    tracker._data["US500"]["tp1_hit"] = True
    tracker._data["US500"]["stop_loss"] = 5000.0  # breakeven

    class FakeFeed:
        def get_current_price(self, instrument):
            return 4999.0  # pulled back to breakeven stop after TP1

    tracker.evaluate_all(now + dt.timedelta(minutes=30), FakeFeed())
    assert any("breakeven stop hit" in m for m in sent)
    assert "US500" not in tracker._data


def test_open_trade_tracker_session_cutoff_closes_us_index_not_crypto(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    tracker = ma.OpenTradeTracker(path=str(tmp_path / "open_trades.json"))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)
    tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0,
                 "stop_loss": 4980.0, "tp1": 5040.0, "tp2": 5060.0}, now)
    tracker.add({"instrument": "BTCUSD", "direction": "BUY", "entry_price": 60000.0,
                 "stop_loss": 59000.0, "tp1": 61000.0, "tp2": 62000.0}, now)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5010.0 if instrument == "US500" else 60100.0  # neither TP/stop touched

    past_hard_flat = dt.datetime(2026, 7, 1, 18, 30, tzinfo=dt.timezone.utc)
    tracker.evaluate_all(past_hard_flat, FakeFeed())
    assert any("US500" in m and "18:30" in m for m in sent)
    assert "US500" not in tracker._data
    assert "BTCUSD" in tracker._data  # crypto is never subject to the US-index session cutoff


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


def test_active_entry_tracker_fast_mode_expires_sooner(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(ma, "send_telegram", lambda text: sent.append(text))
    now = dt.datetime(2026, 7, 1, 10, 0, tzinfo=dt.timezone.utc)

    class FakeFeed:
        def get_current_price(self, instrument):
            return 5050.0  # never touched the entry

    default_tracker = ma.ActiveEntryTracker(path=str(tmp_path / "entries_default.json"))
    default_tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0}, now)
    default_tracker.evaluate_all(now + dt.timedelta(minutes=41), FakeFeed())
    assert "US500" in default_tracker._data  # default 120-min expiry not yet reached

    fast_tracker = ma.ActiveEntryTracker(path=str(tmp_path / "entries_fast.json"))
    fast_tracker.add({"instrument": "US500", "direction": "BUY", "entry_price": 5000.0}, now)
    fast_tracker.evaluate_all(now + dt.timedelta(minutes=41), FakeFeed(), mode=modes.FAST)
    assert "US500" not in fast_tracker._data  # fast mode's 40-min expiry reached
    assert any("entry expired" in m for m in sent)


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
        "stop_loss": 5398.0, "tp1": 5464.0, "tp2": 5508.0, "rr_ratio": 2.0,
        "score": 82, "htf_bias": "TRENDING_UP",
        "breakdown": {"pattern": "LIQUIDITY_SWEEP_BOS", "pdh_pdl": "PDH"},
    }
    now = dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc)
    body = ma.format_aplus_alert(scored, now)
    assert "A+ SIGNAL — US500" in body
    assert "move stop loss to breakeven" in body
    assert "TP2 not hit before 18:30 UTC" in body
    assert "1:2" in body


def test_daily_reset_if_needed_resets_new_day():
    state = {"aplus_count_date": "2026-06-30", "aplus_count": 5}
    ma.daily_reset_if_needed(state, dt.datetime(2026, 7, 1, 0, 0, tzinfo=dt.timezone.utc))
    assert state["aplus_count"] == 0
    assert state["aplus_count_date"] == "2026-07-01"


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
