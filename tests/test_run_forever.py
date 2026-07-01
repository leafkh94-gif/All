import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "12345")

import run_forever as rf


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
