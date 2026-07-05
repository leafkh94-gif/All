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
