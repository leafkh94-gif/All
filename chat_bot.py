"""
INTERACTIVE TELEGRAM BOT — talks back to you.
Reuses all the detection from your main.py (keep both files in one folder).

RUN (must stay running — use your always-on Render host, not a 15-min cron):
    python chat_bot.py

Commands you text it:
    /scan    check Gold, BTC and US indices right now -> get the read
    /status  what it's watching
    /help    menu
"""
import os, time, requests
from datetime import datetime, timezone
import main as bot            # <-- reuses your main.py (same folder)

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

HELP = ("Commands:\n"
        "/scan  - check the markets right now\n"
        "/status - what I'm watching\n"
        "/help  - this menu")

def reply(chat_id, text):
    requests.post(f"{API}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=20)

def scan_now():
    now = datetime.now(timezone.utc)
    cands = []
    for name, sym in bot.SYMBOLS.items():
        if not bot.market_open(name, now):
            continue
        s = bot.detect_setup(bot.get_candles(sym, "15min"))
        if not s:
            continue
        s["instrument"] = name
        s["trend_1h"] = bot.trend_of(bot.get_candles(sym, "1h"))
        s["trend_4h"] = bot.trend_of(bot.get_candles(sym, "4h"))
        cands.append(s)
    if not cands:
        return "No clean setup right now across Gold, BTC or the indices. Patience is the edge."
    bundle = {"time_utc": now.isoformat(), "setups": cands,
              "news": bot.get_news(), "events": bot.get_calendar()}
    return bot.analyze(bundle)

def handle(chat_id, text):
    t = text.strip().lower()
    if t in ("/start", "/help"):
        reply(chat_id, "Hi! I'm your market analyst. I read Gold, Bitcoin and the US "
                       "indices and suggest setups — never a promise they'll win.\n\n" + HELP)
    elif t == "/scan":
        reply(chat_id, "Scanning Gold, BTC and US indices… one moment.")
        reply(chat_id, scan_now())
    elif t == "/status":
        reply(chat_id, "Watching: " + ", ".join(bot.SYMBOLS) +
                       "\nRules: 2-to-1, 2% risk, busy hours, same-session only."
                       "\nReminder: paper-test 30 trades before real money.")
    else:
        reply(chat_id, "I didn't catch that.\n\n" + HELP)

def listen():
    print("Listening for your messages… (Ctrl+C to stop)")
    offset = None
    while True:
        try:
            r = requests.get(f"{API}/getUpdates",
                             params={"timeout": 30, "offset": offset}, timeout=40)
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                if chat and text:
                    handle(chat, text)
        except Exception as e:
            print("error:", e)
            time.sleep(3)

if __name__ == "__main__":
    listen()
