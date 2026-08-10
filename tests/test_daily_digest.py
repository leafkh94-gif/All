import datetime as dt
import json
import os

import pytest

import strategy_config as cfg
from main_alerts import (
    ActiveEntryTracker,
    daily_digest_text,
    maybe_send_daily_digest,
    _append_trade_log,
    TRADE_LOG_MAX_ENTRIES,
)

_NOW = dt.datetime(2026, 8, 5, 18, 30, tzinfo=dt.timezone.utc)
_TODAY = "2026-08-05"


# ─── helpers ──────────────────────────────────────────────────────────────────

def _log(tmp_path, entries):
    path = str(tmp_path / "trade_log.json")
    with open(path, "w") as f:
        json.dump({"entries": entries}, f)
    return path


def _entry(outcome, r_multiple, instrument="US500", direction="BUY", closed_at=None):
    return {
        "instrument": instrument,
        "pattern": "liquidity_sweep",
        "direction": direction,
        "outcome": outcome,
        "r_multiple": r_multiple,
        "closed_at": (closed_at or _NOW).isoformat(),
    }


# ─── daily_digest_text ────────────────────────────────────────────────────────

def test_digest_empty(tmp_path):
    path = _log(tmp_path, [])
    text = daily_digest_text(_NOW, path=path)
    assert "No alerts concluded today" in text
    assert _NOW.strftime("%d %b %Y") in text


def test_digest_win_shows_in_profit_section(tmp_path):
    path = _log(tmp_path, [_entry("tp3_runner_complete", 3.8)])
    text = daily_digest_text(_NOW, path=path)
    assert "✅ Took profit" in text
    assert "US500 BUY" in text
    assert "+3.8R" in text
    assert "❌" not in text
    assert "⏳" not in text


def test_digest_loss_shows_in_stopped_section(tmp_path):
    path = _log(tmp_path, [_entry("stop_before_tp1", -1.0)])
    text = daily_digest_text(_NOW, path=path)
    assert "❌ Stopped out" in text
    assert "-1.0R" in text
    assert "✅" not in text


def test_digest_no_fill_expired(tmp_path):
    path = _log(tmp_path, [_entry("no_fill_expired", 0.0)])
    text = daily_digest_text(_NOW, path=path)
    assert "⏳ Never entered" in text
    assert "Price never reached our entry" in text
    assert "✅" not in text
    assert "❌" not in text


def test_digest_no_fill_sweep_violated(tmp_path):
    path = _log(tmp_path, [_entry("no_fill_sweep_violated", 0.0)])
    text = daily_digest_text(_NOW, path=path)
    assert "⏳" in text
    assert "key level was broken" in text


def test_digest_no_fill_left_without_us(tmp_path):
    path = _log(tmp_path, [_entry("no_fill_left_without_us", 0.0)])
    text = daily_digest_text(_NOW, path=path)
    assert "⏳" in text
    assert "moved away without filling" in text


def test_digest_mixed_all_three_sections(tmp_path):
    path = _log(tmp_path, [
        _entry("tp3_runner_complete", 3.8),
        _entry("stop_before_tp1", -1.0, instrument="US100"),
        _entry("no_fill_expired", 0.0, instrument="BTCUSD"),
    ])
    text = daily_digest_text(_NOW, path=path)
    assert "✅ Took profit" in text
    assert "❌ Stopped out" in text
    assert "⏳ Never entered" in text
    assert "1 win" in text
    assert "1 loss" in text
    assert "1 never filled" in text
    assert "+2.8R" in text  # 3.8 - 1.0


def test_digest_filters_out_yesterday_entries(tmp_path):
    yesterday = _NOW - dt.timedelta(days=1)
    path = _log(tmp_path, [_entry("tp3_runner_complete", 3.8, closed_at=yesterday)])
    text = daily_digest_text(_NOW, path=path)
    assert "No alerts concluded today" in text


def test_digest_net_result_only_counts_filled_trades(tmp_path):
    path = _log(tmp_path, [
        _entry("tp3_runner_complete", 4.0),
        _entry("no_fill_expired", 0.0, instrument="US100"),
    ])
    text = daily_digest_text(_NOW, path=path)
    assert "Net result: +4.0R" in text


def test_digest_breakeven_after_tp1_is_a_win(tmp_path):
    path = _log(tmp_path, [_entry("breakeven_after_tp1", 1.2)])
    text = daily_digest_text(_NOW, path=path)
    assert "✅ Took profit" in text
    assert "TP1 hit, rest closed at breakeven" in text


# ─── maybe_send_daily_digest ──────────────────────────────────────────────────

def test_digest_fires_at_hard_flat_time(tmp_path):
    messages = []
    path = _log(tmp_path, [])
    state = {}
    _send = lambda t: messages.append(t)
    import main_alerts as ma
    orig = ma.send_telegram
    ma.send_telegram = _send
    try:
        maybe_send_daily_digest(state, _NOW, path=path)
    finally:
        ma.send_telegram = orig
    assert len(messages) == 1
    assert state["last_daily_digest_date"] == _TODAY


def test_digest_does_not_fire_before_hard_flat(tmp_path):
    messages = []
    path = _log(tmp_path, [])
    state = {}
    before = _NOW.replace(hour=17, minute=0)
    import main_alerts as ma
    orig = ma.send_telegram
    ma.send_telegram = lambda t: messages.append(t)
    try:
        maybe_send_daily_digest(state, before, path=path)
    finally:
        ma.send_telegram = orig
    assert messages == []
    assert "last_daily_digest_date" not in state


def test_digest_does_not_fire_twice_same_day(tmp_path):
    messages = []
    path = _log(tmp_path, [])
    state = {"last_daily_digest_date": _TODAY}
    import main_alerts as ma
    orig = ma.send_telegram
    ma.send_telegram = lambda t: messages.append(t)
    try:
        maybe_send_daily_digest(state, _NOW, path=path)
    finally:
        ma.send_telegram = orig
    assert messages == []


# ─── ActiveEntryTracker no-fill logging ───────────────────────────────────────

def _make_entry(instrument="US500", direction="BUY", alert_time=None):
    return {
        "direction": direction,
        "entry_price": 5420.0,
        "stop_loss": 5398.0,
        "tp1": 5464.0,
        "tp2": 5508.0,
        "tp3": 5552.0,
        "leg_origin": 5380.0,
        "leg_end": 5450.0,
        "pattern": "liquidity_sweep",
        "alert_time": (alert_time or _NOW).isoformat(),
    }


def test_cancel_sweep_violated_logs_no_fill(tmp_path):
    tracker_path = str(tmp_path / "entries.json")
    log_path = str(tmp_path / "trade_log.json")
    with open(tracker_path, "w") as f:
        json.dump({"US500": _make_entry()}, f)

    import main_alerts as ma
    orig_send = ma.send_telegram
    orig_log_path = ma.TRADE_LOG_PATH
    ma.send_telegram = lambda t: None
    ma.TRADE_LOG_PATH = log_path
    try:
        tracker = ActiveEntryTracker(path=tracker_path)
        entry = tracker._data["US500"]
        tracker._cancel("US500", entry, "SWEEP_VIOLATED", _NOW)
    finally:
        ma.send_telegram = orig_send
        ma.TRADE_LOG_PATH = orig_log_path

    with open(log_path) as f:
        logged = json.load(f)["entries"]
    assert len(logged) == 1
    assert logged[0]["outcome"] == "no_fill_sweep_violated"
    assert logged[0]["r_multiple"] == 0.0
    assert logged[0]["instrument"] == "US500"


def test_expired_entry_logs_no_fill(tmp_path):
    tracker_path = str(tmp_path / "entries.json")
    log_path = str(tmp_path / "trade_log.json")
    old_time = _NOW - dt.timedelta(minutes=cfg.PENDING_ORDER_MAX_MINUTES + 5)
    with open(tracker_path, "w") as f:
        json.dump({"US500": _make_entry(alert_time=old_time)}, f)

    class _DummyFeed:
        def get_current_price(self, instrument):
            return 5430.0  # above entry but not touching for BUY (entry=5420 is limit buy so BUY fills when price <= entry)

        def get_candles(self, instrument, timeframe, n=2):
            p = self.get_current_price(instrument)
            return [{"o": p, "h": p, "l": p, "c": p}]

    import main_alerts as ma
    orig_send = ma.send_telegram
    orig_log_path = ma.TRADE_LOG_PATH
    ma.send_telegram = lambda t: None
    ma.TRADE_LOG_PATH = log_path
    try:
        tracker = ActiveEntryTracker(path=tracker_path)
        tracker.evaluate_all(_NOW, _DummyFeed())
    finally:
        ma.send_telegram = orig_send
        ma.TRADE_LOG_PATH = orig_log_path

    with open(log_path) as f:
        logged = json.load(f)["entries"]
    assert len(logged) == 1
    assert logged[0]["outcome"] == "no_fill_expired"
    assert logged[0]["r_multiple"] == 0.0
