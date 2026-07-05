import datetime as dt

import main_alerts as ma
from strategy import modes


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
