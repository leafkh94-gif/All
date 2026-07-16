import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "12345")

import main_alerts as ma
import run_forever as rf
from strategy import modes


def test_next_scan_timestamp_lands_on_quarter_hour():
    # 2026-07-01 12:07:33 UTC -> next boundary 12:15:00
    ts = 1782907653  # arbitrary; verify alignment property instead of a constant
    nxt = rf.next_scan_timestamp(ts)
    assert nxt > ts
    assert nxt % (15 * 60) == 0
    assert nxt - ts <= 15 * 60


def test_next_scan_timestamp_on_exact_boundary_moves_forward():
    boundary = (1782907653 // 900 + 1) * 900
    assert rf.next_scan_timestamp(boundary) == boundary + 900


def test_next_scan_timestamp_respects_explicit_interval_override():
    ts = 1782907653
    nxt = rf.next_scan_timestamp(ts, interval_minutes=5)
    assert nxt > ts
    assert nxt % (5 * 60) == 0
    assert nxt - ts <= 5 * 60


def test_next_scan_timestamp_uses_active_mode_when_no_override(monkeypatch):
    monkeypatch.setattr(ma, "load_active_mode", lambda: modes.FAST)
    ts = 1782907653
    nxt = rf.next_scan_timestamp(ts)
    assert nxt % (5 * 60) == 0


def test_handle_command_status_replies(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(rf, "status_text", lambda: "STATUS")
    rf.handle_command("/status")
    assert sent == ["STATUS"]


def test_handle_command_help_replies(monkeypatch):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    rf.handle_command("/help")
    assert any("/scan" in m for m in sent)


def test_handle_command_scan_runs_scan(monkeypatch):
    sent, scans = [], []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(rf, "run_scan_safely", lambda trigger: scans.append(trigger) or True)
    monkeypatch.setattr(rf, "status_text", lambda: "STATUS")
    rf.handle_command("/scan")
    assert scans == ["manual"]
    assert any("Scan complete" in m for m in sent)


def test_unknown_command_is_ignored(monkeypatch):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    rf.handle_command("hello there")
    assert sent == []


def test_handle_command_mode_shows_current(monkeypatch):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "load_active_mode", lambda: modes.STANDARD)
    rf.handle_command("/mode")
    assert any("standard" in m for m in sent)
    assert any("loose" in m and "fast" in m for m in sent)


def test_handle_command_mode_switches_and_persists(monkeypatch):
    sent, saved = [], []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "save_active_mode_name", lambda name: saved.append(name))
    rf.handle_command("/mode fast")
    assert saved == ["fast"]
    assert any("fast" in m for m in sent)


def test_handle_command_mode_rejects_unknown_name(monkeypatch):
    sent, saved = [], []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "save_active_mode_name", lambda name: saved.append(name))
    rf.handle_command("/mode bogus")
    assert saved == []
    assert any("Unknown mode" in m for m in sent)


def test_handle_command_mode_switches_to_swing(monkeypatch):
    sent, saved = [], []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "save_active_mode_name", lambda name: saved.append(name))
    rf.handle_command("/mode swing")
    assert saved == ["swing"]
    assert any("swing" in m for m in sent)


def test_help_text_mentions_swing_mode():
    assert "swing" in rf.help_text()


def test_handle_command_loss_logs_and_confirms(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "record_loss", lambda amount: 15.0)
    rf.handle_command("/loss 15")
    assert any("Logged $15.00 loss" in m for m in sent)


def test_handle_command_loss_alone_reports_current_total(monkeypatch):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "load_json", lambda path: {"daily_loss_total": 12.0})
    rf.handle_command("/loss")
    assert any("$12.00" in m for m in sent)


def test_handle_command_loss_rejects_bad_number(monkeypatch):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    rf.handle_command("/loss abc")
    assert any("Usage" in m for m in sent)


def test_handle_command_win_reduces_total(monkeypatch):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "record_win", lambda amount: -5.0)
    rf.handle_command("/win 10")
    assert any("Logged $10.00 win" in m for m in sent)


def test_handle_command_blackout_sets_it(monkeypatch):
    sent, saved = [], []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "set_blackout", lambda minutes: saved.append(minutes))
    rf.handle_command("/blackout 30")
    assert saved == [30.0]
    assert any("30 min" in m for m in sent)


def test_handle_command_blackout_off_clears_it(monkeypatch):
    sent, cleared = [], []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "clear_blackout", lambda: cleared.append(True))
    rf.handle_command("/blackout off")
    assert cleared == [True]
    assert any("cleared" in m for m in sent)


def test_handle_command_blackout_alone_reports_inactive(monkeypatch):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(ma, "load_json", lambda path: {})
    rf.handle_command("/blackout")
    assert any("No blackout active" in m for m in sent)


def test_handle_command_blackout_rejects_bad_number(monkeypatch):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    rf.handle_command("/blackout abc")
    assert any("Usage" in m for m in sent)


def test_performance_text_reports_no_trades(monkeypatch):
    monkeypatch.setattr(ma, "load_json", lambda path: {})
    text = rf.performance_text()
    assert "No closed trades logged" in text


def test_performance_text_groups_by_pattern_with_win_rate_and_avg_r(monkeypatch):
    entries = [
        {"pattern": "FLAG", "r_multiple": 2.0},
        {"pattern": "FLAG", "r_multiple": -1.0},
        {"pattern": "HEAD_SHOULDERS", "r_multiple": -1.0},
    ]
    monkeypatch.setattr(ma, "load_json", lambda path: {"entries": entries})
    text = rf.performance_text()
    assert "Overall: 3 trades" in text
    assert "FLAG: 2 trades, 50% win rate" in text
    assert "HEAD_SHOULDERS: 1 trades, 0% win rate" in text


def test_performance_text_groups_unknown_pattern(monkeypatch):
    monkeypatch.setattr(ma, "load_json", lambda path: {"entries": [{"pattern": None, "r_multiple": 1.0}]})
    text = rf.performance_text()
    assert "unknown: 1 trades" in text


def test_handle_command_performance_replies(monkeypatch):
    sent = []
    monkeypatch.setattr(rf, "reply", lambda text: sent.append(text))
    monkeypatch.setattr(rf, "performance_text", lambda: "PERF")
    rf.handle_command("/performance")
    assert sent == ["PERF"]


def test_diagnostics_text_no_pattern_never_renders_none_none(monkeypatch):
    # Reproduces the exact live-bot output that surfaced the regression: a
    # dynamic "no pattern detected (...)" blocked message (introduced when
    # scan_diagnostics.py started folding bar-count/staleness detail into it)
    # must not fall through to the "direction pattern — blocked" branch, which
    # would render the literal string "None None" since neither is known yet.
    monkeypatch.setattr(ma, "load_json", lambda path: {
        "last_diagnostics": {
            "BTCUSD": {
                "pattern": None, "direction": None, "score": None,
                "blocked": "no pattern detected (80 bars OK, last candle age=-226min, fresh — detectors too tight)",
            }
        }
    })
    text = rf.diagnostics_text()
    assert "None None" not in text
    assert "no pattern detected" in text
