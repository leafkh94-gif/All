"""
Trading Alert Bot — always-on real-time runner.

GitHub Actions cron cannot hold a 15-minute cadence (free-tier schedules drift
by hours), so this process is the real deployment target: it never exits,
scans exactly at :00/:15/:30/:45 UTC, and between scans long-polls Telegram so
you can talk to the bot in real time.

Commands you can text the bot:
    /scan    run a full scan right now and get the read
    /status  active WATCHes, pending A+ confirmations, last scan time
    /mode    show or switch trading mode (standard/loose/fast)
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
from strategy import modes

TELEGRAM_API = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}"
CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])

# 0 = run forever (dedicated host). On GitHub Actions each relay job sets this
# below the 6h job limit so the process exits cleanly and the next job takes over.
MAX_RUNTIME_MINUTES = float(os.environ.get("MAX_RUNTIME_MINUTES", "0"))
# Suppress the "Bot online" greeting (set on relay jobs so a restart every
# ~6h doesn't spam the chat).
QUIET_START = os.environ.get("QUIET_START", "") == "1"

OFFSET_PATH = os.path.join(ma.STATE_DIR, "telegram_offset.json")


def help_text():
    m = ma.load_active_mode()
    return (
        "Commands:\n"
        "/scan - run a full scan right now\n"
        "/status - active WATCHes and last scan\n"
        "/mode - show or switch mode (standard/loose/fast)\n"
        "/help - this menu\n\n"
        f"Mode: {m.name} — scans every {m.scan_interval_minutes} min "
        f"on a {m.entry_timeframe} entry timeframe."
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
    mode_name = main_state.get("last_scan_mode") or ma.load_active_mode().name
    lines = [f"📊 Bot status — {datetime.now(timezone.utc).strftime('%H:%M')} UTC",
             f"Mode: {mode_name}",
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


def diagnostics_text():
    main_state = ma.load_json(ma.MAIN_STATE_PATH)
    diag = main_state.get("last_diagnostics")
    if not diag:
        return "No scan diagnostics yet."
    lines = ["📈 Scan detail (why each instrument didn't qualify):"]
    for instrument, d in diag.items():
        if d["blocked"] == "no pattern detected":
            lines.append(f"  {instrument}: no pattern detected")
        elif d["score"] is None:
            lines.append(f"  {instrument}: {d['direction']} {d['pattern']} — {d['blocked']}")
        elif d["blocked"] is None:
            lines.append(f"  {instrument}: {d['direction']} {d['pattern']} — {d['score']}/100 ✅ qualified")
        else:
            lines.append(f"  {instrument}: {d['direction']} {d['pattern']} — {d['score']}/100 ({d['blocked']})")
    return "\n".join(lines)


def run_scan_safely(trigger):
    try:
        ma.run()
        print(f"[{trigger}] scan ok — {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
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
                  f"Any qualifying WATCH/A+ alerts were sent above.\n\n{status_text()}\n\n"
                  f"{diagnostics_text()}")
        else:
            reply("⚠️ Scan hit an error — check the host logs. Will retry on the next cycle.")
    elif t.startswith("/status"):
        reply(status_text())
    elif t.startswith("/mode"):
        parts = text.strip().split(maxsplit=1)
        if len(parts) == 1:
            current = ma.load_active_mode().name
            reply(f"Current mode: {current}\nAvailable: standard, loose, fast\n"
                  f"Usage: /mode <name> to switch.")
        else:
            requested = parts[1].strip().lower()
            if requested not in modes.MODES:
                reply(f"Unknown mode '{requested}'. Available: standard, loose, fast")
            else:
                ma.save_active_mode_name(requested)
                new_mode = modes.MODES[requested]
                reply(f"✅ Mode set to '{requested}'. Takes effect on the next scan cycle "
                      f"(every {new_mode.scan_interval_minutes} min).")
    elif t.startswith(("/help", "/start")):
        reply(help_text())


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


def next_scan_timestamp(now_ts=None, interval_minutes=None):
    """Next scan-cadence boundary (e.g. :00/:15/:30/:45 UTC at the default
    15-min interval) as a unix timestamp. interval_minutes defaults to the
    currently active mode's cadence when not given explicitly."""
    now_ts = now_ts if now_ts is not None else time.time()
    if interval_minutes is None:
        interval_minutes = ma.load_active_mode().scan_interval_minutes
    interval = interval_minutes * 60
    return (int(now_ts) // interval + 1) * interval


def main():
    deadline = time.time() + MAX_RUNTIME_MINUTES * 60 if MAX_RUNTIME_MINUTES else None
    print(f"bot starting — real-time mode"
          + (f" (bounded, {MAX_RUNTIME_MINUTES:.0f} min)" if deadline else ""))
    if not QUIET_START:
        reply("🤖 Bot online — real-time mode.\n" + help_text())
    run_scan_safely("startup")
    next_scan = next_scan_timestamp()
    current_interval = ma.load_active_mode().scan_interval_minutes
    while True:
        if deadline and time.time() >= deadline:
            print("max runtime reached — exiting cleanly for the next relay job")
            return
        remaining = next_scan - time.time()
        if remaining <= 0:
            run_scan_safely("scheduled")
            next_scan = next_scan_timestamp()
            current_interval = ma.load_active_mode().scan_interval_minutes
            continue
        # long-poll Telegram in <=45s slices until the next scan boundary
        slice_s = min(45, max(1, remaining))
        if deadline:
            slice_s = min(slice_s, max(1, deadline - time.time()))
        poll_telegram(int(slice_s))
        new_interval = ma.load_active_mode().scan_interval_minutes
        if new_interval != current_interval:
            next_scan = next_scan_timestamp()  # mode changed mid-wait — rebase to new cadence
            current_interval = new_interval
        mins_left = max(0, int((next_scan - time.time()) // 60))
        print(f"alive — listening on Telegram, next scan in ~{mins_left} min")


if __name__ == "__main__":
    main()
