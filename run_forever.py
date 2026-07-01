"""
Trading Alert Bot — always-on real-time runner.

GitHub Actions cron cannot hold a 15-minute cadence (free-tier schedules drift
by hours), so this process is the real deployment target: it never exits,
scans exactly at :00/:15/:30/:45 UTC, and between scans long-polls Telegram so
you can talk to the bot in real time.

Commands you can text the bot:
    /scan    run a full scan right now and get the read
    /status  active WATCHes, pending A+ confirmations, last scan time
    /help    this menu

RUN (must stay running — Render/Railway/VPS worker, NOT a 15-min cron):
    python run_forever.py
"""
import json
import os
import time
import traceback
from datetime import datetime, timezone

import requests

import main_alerts as ma
import scoring_strategy as strat
import strategy_config as cfg

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])

OFFSET_PATH = os.path.join(ma.STATE_DIR, "telegram_offset.json")

HELP_TEXT = (
    "Commands:\n"
    "/scan - run a full scan right now\n"
    "/status - active WATCHes and last scan\n"
    "/help - this menu\n\n"
    f"Automatic scans run every {cfg.SCAN_INTERVAL_MINUTES} min at :00/:15/:30/:45 UTC."
)


def reply(text):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": text}, timeout=20)
    except requests.RequestException:
        pass


def status_text():
    main_state = ma.load_json(ma.MAIN_STATE_PATH)
    watches = ma.load_json(os.path.join(ma.STATE_DIR, "watches.json"))
    pending = strat.PendingAPlusStore().all()
    lines = [f"📊 Bot status — {datetime.now(timezone.utc).strftime('%H:%M')} UTC",
             f"Last scan: {main_state.get('last_scan_time', 'n/a')}",
             f"Today's A+ signals: {main_state.get('aplus_count', 0)}"]
    if watches:
        lines.append("Active WATCHes:")
        for inst, w in watches.items():
            lines.append(f"  ⚡ {inst} {w['direction']} — score {w['score']}, entry {w['entry_price']}")
    else:
        lines.append("Active WATCHes: none")
    if pending:
        lines.append("Awaiting A+ confirmation: " + ", ".join(pending))
    return "\n".join(lines)


def run_scan_safely(trigger):
    try:
        ma.run()
        return True
    except Exception:
        print(f"[{trigger}] scan failed:\n{traceback.format_exc()}")
        return False


def handle_command(text):
    t = text.strip().lower()
    if t.startswith("/scan"):
        reply("🔍 Scanning all instruments now...")
        ok = run_scan_safely("manual")
        main_state = ma.load_json(ma.MAIN_STATE_PATH)
        if ok:
            reply(f"Scan complete ({main_state.get('last_scan_time', 'n/a')}). "
                  f"Any qualifying WATCH/A+ alerts were sent above.\n\n{status_text()}")
        else:
            reply("⚠️ Scan hit an error — check the host logs. Will retry on the next cycle.")
    elif t.startswith("/status"):
        reply(status_text())
    elif t.startswith(("/help", "/start")):
        reply(HELP_TEXT)


def _load_offset():
    return ma.load_json(OFFSET_PATH).get("offset", 0)


def _save_offset(offset):
    ma.save_json(OFFSET_PATH, {"offset": offset})


def poll_telegram(timeout_s):
    """Long-poll getUpdates for up to timeout_s seconds, handling any commands
    from the configured chat. Returns quickly if a message arrives."""
    offset = _load_offset()
    try:
        r = requests.get(f"{TELEGRAM_API}/getUpdates",
                         params={"timeout": timeout_s, "offset": offset + 1,
                                 "allowed_updates": '["message"]'},
                         timeout=timeout_s + 10)
        updates = r.json().get("result", [])
    except requests.RequestException:
        time.sleep(5)
        return
    for u in updates:
        offset = max(offset, u["update_id"])
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != CHAT_ID:
            continue
        text = msg.get("text", "")
        if text:
            handle_command(text)
    if updates:
        _save_offset(offset)


def next_scan_timestamp(now_ts=None):
    """Next :00/:15/:30/:45 UTC boundary as a unix timestamp."""
    now_ts = now_ts if now_ts is not None else time.time()
    interval = cfg.SCAN_INTERVAL_MINUTES * 60
    return (int(now_ts) // interval + 1) * interval


def main():
    print("bot starting — real-time mode")
    reply("🤖 Bot online — real-time mode.\n" + HELP_TEXT)
    run_scan_safely("startup")
    next_scan = next_scan_timestamp()
    while True:
        remaining = next_scan - time.time()
        if remaining <= 0:
            run_scan_safely("scheduled")
            next_scan = next_scan_timestamp()
            continue
        # long-poll Telegram in <=45s slices until the next quarter-hour boundary
        poll_telegram(int(min(45, max(1, remaining))))


if __name__ == "__main__":
    main()
