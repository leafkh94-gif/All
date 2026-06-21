"""
═══════════════════════════════════════════════════════════════════════
  MARKET-EXPERT BOT — single-file build
  Watches Gold, Bitcoin & US indices on 15m/1h/4h, reads the news,
  detects setups in Python, and asks Claude (a 10-yr analyst) to judge,
  rank and explain. Sends the best read to Telegram.

  IT SUGGESTS. IT DOES NOT PROMISE WINNERS. Paper-test 30 trades first.
═══════════════════════════════════════════════════════════════════════

SETUP (once):
    pip install anthropic requests pandas

SECRETS (set as environment variables — never hard-code):
    ANTHROPIC_API_KEY   CAPITAL_API_KEY   CAPITAL_EMAIL   CAPITAL_PASSWORD
    NEWSAPI_KEY   TELEGRAM_BOT_TOKEN   TELEGRAM_CHAT_ID

RUN:
    python main.py
    (schedule every 15 min on Render, or GitHub Actions — see the bottom)
"""
import os, json, time, requests
import pandas as pd
from datetime import datetime, timezone
from anthropic import Anthropic

# ─────────────────────────────────────────────────────────────────────
# 1) CONFIG
# ─────────────────────────────────────────────────────────────────────
CAPITAL_KEY   = os.environ["CAPITAL_API_KEY"]
CAPITAL_EMAIL = os.environ["CAPITAL_EMAIL"]
CAPITAL_PASS  = os.environ["CAPITAL_PASSWORD"]
CAPITAL_BASE  = "https://api-capital.backend.gbgroupplc.com/api/v1"

# Capital.com epic names
SYMBOLS = {"BTC": "BITCOIN", "Gold": "GOLD",
           "US500": "US500", "US100": "US100", "US30": "US30"}

# Capital.com resolution strings
RESOLUTION = {"15min": "MINUTE_15", "1h": "HOUR", "4h": "HOUR_4"}

CACHE_DIR = ".cache"; os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_TTL = {"15min": 0, "1h": 3600, "4h": 14400}  # 15m always fresh; 1h/4h cached
STATE = os.path.join(CACHE_DIR, "state.json")
COOLDOWN = 2 * 3600                                  # 2h between same-setup alerts

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ─────────────────────────────────────────────────────────────────────
# 2) DATA  (Capital.com — session auth + caching for 1h/4h)
# ─────────────────────────────────────────────────────────────────────
_cap_session = {"cst": None, "token": None}

def _open_session():
    r = requests.post(f"{CAPITAL_BASE}/session",
        headers={"X-CAP-API-KEY": CAPITAL_KEY, "Content-Type": "application/json"},
        json={"identifier": CAPITAL_EMAIL, "password": CAPITAL_PASS}, timeout=20)
    r.raise_for_status()
    _cap_session["cst"]   = r.headers["CST"]
    _cap_session["token"] = r.headers["X-SECURITY-TOKEN"]

def _headers():
    return {"X-CAP-API-KEY": CAPITAL_KEY,
            "CST": _cap_session["cst"],
            "X-SECURITY-TOKEN": _cap_session["token"]}

def _path(sym, interval):
    return os.path.join(CACHE_DIR, f"{sym}_{interval}.json")

def get_candles(symbol, interval, n=60):
    ttl = CACHE_TTL.get(interval, 0)
    p = _path(symbol, interval)
    if ttl and os.path.exists(p) and time.time() - os.path.getmtime(p) < ttl:
        return json.load(open(p))
    res = RESOLUTION[interval]
    r = requests.get(f"{CAPITAL_BASE}/prices/{symbol}",
        headers=_headers(), params={"resolution": res, "max": n}, timeout=20)
    if r.status_code == 401:          # session expired — re-auth once
        _open_session()
        r = requests.get(f"{CAPITAL_BASE}/prices/{symbol}",
            headers=_headers(), params={"resolution": res, "max": n}, timeout=20)
    data = r.json().get("prices", [])
    candles = [{"t": c["snapshotTime"],
                "o": float(c["openPrice"]["bid"]),
                "h": float(c["highPrice"]["bid"]),
                "l": float(c["lowPrice"]["bid"]),
                "c": float(c["closePrice"]["bid"])} for c in data]
    if candles:
        json.dump(candles, open(p, "w"))
    return candles

# ─────────────────────────────────────────────────────────────────────
# 3) INDICATORS + SETUP DETECTION  (ALL risk math here — never the LLM)
# ─────────────────────────────────────────────────────────────────────
def _df(c): return pd.DataFrame(c)

def atr(df, period=14):
    pc = df["c"].shift(1)
    tr = pd.concat([df["h"] - df["l"], (df["h"] - pc).abs(), (df["l"] - pc).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()

def ema(s, span): return s.ewm(span=span, adjust=False).mean()

def trend_of(candles):
    df = _df(candles)
    if len(df) < 25: return "unknown"
    return "up" if df["c"].iloc[-1] > ema(df["c"], 20).iloc[-1] else "down"

def detect_setup(candles15):
    df = _df(candles15)
    if len(df) < 30: return None
    df["atr"] = atr(df); df["ema20"] = ema(df["c"], 20)
    df["atr_ma"] = df["atr"].rolling(5).mean()
    last, prev = df.iloc[-1], df.iloc[-2]
    a = last["atr"]
    if pd.isna(a) or a <= 0: return None

    vol_expanding = last["atr"] > last["atr_ma"]          # ATR-breakout filter
    up   = last["c"] > last["ema20"];  down = last["c"] < last["ema20"]
    broke_high = last["c"] > prev["h"]; broke_low = last["c"] < prev["l"]
    direction = "BUY" if (up and broke_high) else "SELL" if (down and broke_low) else None
    if not direction: return None

    entry = last["c"]
    if direction == "BUY":
        chase = (entry - prev["h"]) / a; stop, target = entry - a, entry + 2*a
    else:
        chase = (prev["l"] - entry) / a; stop, target = entry + a, entry - 2*a
    if chase > 0.5: return None                            # chasing — skip

    trend = min(40, abs(entry - last["ema20"]) / a * 25)
    trig  = min(30, abs(last["c"] - last["o"]) / a * 30)
    loc   = max(0, 20 - chase * 40)
    vola  = 10 if vol_expanding else 0
    score = int(min(100, round(trend + trig + loc + vola)))

    return {"direction": direction, "entry": float(round(entry, 2)),
            "stop": float(round(stop, 2)), "target": float(round(target, 2)),
            "atr": float(round(a, 2)), "score": int(score),
            "vol_expanding": bool(vol_expanding)}

# ─────────────────────────────────────────────────────────────────────
# 4) MARKET-HOURS GUARD
# ─────────────────────────────────────────────────────────────────────
def market_open(instrument, now=None):
    now = now or datetime.now(timezone.utc)
    wd = now.weekday(); h = now.hour + now.minute/60
    if instrument in ("US500", "US100", "US30"):
        return wd < 5 and 13.5 <= h < 20.0
    if instrument == "Gold":
        if wd == 5: return False
        if wd == 6 and h < 22: return False
        if wd == 4 and h >= 21: return False
        return True
    return True   # BTC 24/7

# ─────────────────────────────────────────────────────────────────────
# 5) COOLDOWN STATE
# ─────────────────────────────────────────────────────────────────────
def load_state():
    try: return json.load(open(STATE))
    except Exception: return {}

def save_state(s): json.dump(s, open(STATE, "w"))

def on_cooldown(state, key, now_ts):
    return key in state and (now_ts - state[key]) < COOLDOWN

# ─────────────────────────────────────────────────────────────────────
# 6) NEWS + CALENDAR
# ─────────────────────────────────────────────────────────────────────
def get_news():
    try:
        r = requests.get("https://newsapi.org/v2/everything", params={
            "q": 'gold OR bitcoin OR Nasdaq OR "Federal Reserve" OR inflation',
            "language": "en", "sortBy": "publishedAt", "pageSize": 6,
            "apiKey": os.environ["NEWSAPI_KEY"]}, timeout=20)
        return [a["title"] for a in r.json().get("articles", [])]
    except Exception:
        return []

def get_calendar():
    try:
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=20)
        return [f'{e["title"]} ({e["date"]})' for e in r.json()
                if e.get("impact") == "High" and e.get("country") == "USD"]
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────────
# 7) THE EXPERT PROMPT  (judgement only — never recomputes numbers)
# ─────────────────────────────────────────────────────────────────────
JUDGE_PROMPT = """You are a trading analyst with 10 years of experience in
gold, Bitcoin, and the US indices. You know every strategy (supply/demand,
liquidity sweep + break of structure, double tops, flags, post-news retests,
trend-following, mean reversion).

You are given ONLY the instruments that ALREADY have a valid 15-minute setup,
detected and PRICED by our system. The entry, stop, target and score are
already calculated correctly in code. DO NOT recompute or change any number.

Your job is JUDGEMENT, not arithmetic:
1. Does the 15m setup agree with the 1h and 4h trend given? If it fights the
   higher timeframe, downgrade or VETO it and say why.
2. News gate: if a high-impact release (CPI/NFP/FOMC) is near, say STAND ASIDE.
3. Rank the valid setups and name the single BEST one, with a one-line reason.
4. Explain each in one or two plain sentences using the given numbers as-is.

Never invent setups for instruments not listed. Never use the words
guaranteed, sure, or high win rate. End each with:
"Setup read - not a promise it will win."

DATA: {data}
"""

def analyze(bundle):
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user",
                   "content": JUDGE_PROMPT.format(data=json.dumps(bundle, indent=2))}])
    return msg.content[0].text

# ─────────────────────────────────────────────────────────────────────
# 8) TELEGRAM
# ─────────────────────────────────────────────────────────────────────
def send_telegram(text):
    requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}, timeout=20)

# ─────────────────────────────────────────────────────────────────────
# 9) MAIN LOOP (one pass — schedule it every 15 min)
# ─────────────────────────────────────────────────────────────────────
def run():
    now = datetime.now(timezone.utc)
    _open_session()          # one auth call per run; re-used by all get_candles calls
    state = load_state()
    candidates = []

    for name, sym in symbols.items():
        if not market_open(name, now):
            continue
        setup = detect_setup(get_candles(sym, "15min"))
        if not setup:
            continue
        key = f"{name}_{setup['direction']}"
        if on_cooldown(state, key, now.timestamp()):
            continue
        setup["instrument"] = name
        setup["trend_1h"] = trend_of(get_candles(sym, "1h"))
        setup["trend_4h"] = trend_of(get_candles(sym, "4h"))
        candidates.append(setup)

    if not candidates:
        print(now.strftime("%H:%M"), "no setup - no message sent")
        return

    bundle = {"time_utc": now.isoformat(), "setups": candidates,
              "news": get_news(), "events": get_calendar()}
    read = analyze(bundle)

    for s in candidates:
        state[f"{s['instrument']}_{s['direction']}"] = now.timestamp()
    save_state(state)

    send_telegram("MARKET READ " + now.strftime("%H:%M UTC") + "\n\n" + read)
    print("alert sent for:", [c["instrument"] for c in candidates])

if __name__ == "__main__":
    run()

# ─────────────────────────────────────────────────────────────────────
# 10) SCHEDULE IT — GitHub Actions (.github/workflows/bot.yml)
# ─────────────────────────────────────────────────────────────────────
# Prefer Render (always-on, keeps .cache). Actions cron drifts 5-20 min.
#
# name: market-expert-bot
# on:
#   schedule:
#     - cron: "*/15 * * * *"
#   workflow_dispatch:
# jobs:
#   run:
#     runs-on: ubuntu-latest
#     steps:
#       - uses: actions/checkout@v4
#       - uses: actions/setup-python@v5
#         with: { python-version: "3.11" }
#       - uses: actions/cache@v4
#         with: { path: .cache, key: bot-cache }
#       - run: pip install anthropic requests pandas
#       - run: python main.py
#         env:
#           ANTHROPIC_API_KEY:  ${{ secrets.ANTHROPIC_API_KEY }}
#           CAPITAL_API_KEY:    ${{ secrets.CAPITAL_API_KEY }}
#           CAPITAL_EMAIL:      ${{ secrets.CAPITAL_EMAIL }}
#           CAPITAL_PASSWORD:   ${{ secrets.CAPITAL_PASSWORD }}
#           NEWSAPI_KEY:        ${{ secrets.NEWSAPI_KEY }}
#           TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
#           TELEGRAM_CHAT_ID:   ${{ secrets.TELEGRAM_CHAT_ID }}
#
# ── BEFORE REAL MONEY: paper-trade the first 30 suggestions, log them,
#    go live only if profitable, at 2% size. That gate protects your $3,000.
