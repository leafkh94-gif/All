# Gold Alert Bot — Golden Trio + SMC

Alert-only trading bot for **XAUUSD (Gold)** on Capital.com CFDs.
Scans every 15 minutes on **two parallel detectors** and sends
WATCH ⚡ / A+ 🟢 alerts to Telegram. **It suggests. It never executes trades.**

1. **Golden Trio** — RSI(14) hook-up from a local low at a 10-bar Turtle
   (Donchian) band, gated by a ZLSMA(30) trend-slope filter. ZLSMA is 30
   not 50 because the Capital.com M15 API caps at ~80 bars per request.
2. **Smart Money Concepts** — Order Block, Change of Character (CHOCH),
   and Liquidity Sweep detection via the `smartmoneyconcepts` library.

Each scan runs both detectors and keeps the higher-quality candidate.

**Targets** (fixed mode, `POINT_VALUE = 1.0`): $25 stop, $25 TP1 (1R),
$50 TP2 (2R), $100 TP3 (4R). Max spread accepted per signal: $1.50
(~6% of stop). Best during the London/NY overlap (13:00–16:00 UTC),
which is gold's prime liquidity window.

## How to run it (real-time mode — recommended)

The bot is designed to run as one always-on process:

```
python run_forever.py
```

This scans exactly at :00/:15/:30/:45 UTC and, between scans, listens to your
Telegram messages in real time:

| Command | What it does |
|---|---|
| `/scan` | run a full scan right now and get the read |
| `/status` | active WATCHes, pending A+ confirmations, last scan time |
| `/help` | command menu |

### Option A — run it on GitHub Actions for free (relay mode)

Because this repo is **public**, Actions minutes are free and unlimited, so
the workflow runs the bot in "relay" mode: each job keeps `run_forever.py`
alive for ~5h40m (GitHub kills jobs at 6h), then dispatches the next job
before exiting, using the run's own automatic token — **no token to create,
no extra secret to add.** All required secrets (`CAPITAL_API_KEY`,
`CAPITAL_EMAIL`, `CAPITAL_PASSWORD`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`) are already configured on this repo. Setup is just:

1. Actions tab → market-expert-bot → **Run workflow** — that's it, the
   chain keeps itself alive from there.

If the "Chain the next relay run" step ever shows a ⚠️ warning in the logs,
this repo's default Actions token permissions are set to read-only, which
blocks self-dispatch. The one-time fix: repo Settings → Actions → General →
scroll to **Workflow permissions** → select **Read and write permissions**
→ Save. No token to generate — it's a single radio button. Until that's
flipped, the hourly cron below still restarts the bot automatically
(just with up to a ~1h gap instead of an immediate handoff).

Known trade-offs of relay mode:

- normally a ~1–3 min blind spot every ~5h40m while jobs hand off; if
  self-dispatch isn't permitted (see above), the gap is up to ~1h until
  the hourly cron catches it instead
- GitHub's terms discourage using Actions as generic always-on compute;
  small bots usually fly under the radar, but GitHub may disable the
  workflow — if that happens, switch to Option B
- the repo must stay **public** (private repos get only 2,000 free
  minutes/month — a day and a half of relay), so never commit secrets

### Option B — any always-on host (Render, Railway, VPS, old laptop)

No gaps, no terms-of-service gray area. In Render: **New → Background
Worker → connect this repo** (it reads `render.yaml` automatically), set
the same env vars, deploy. Or on any machine you own that stays on:
set the env vars and run `python run_forever.py` under
`systemd`/`tmux`. If a host is running the bot, disable the Actions
workflow (Actions → market-expert-bot → ⋯ → Disable workflow) so you
don't get duplicate alerts.

## Environment variables

Never commit these. Set them on the host (Render dashboard, or a local
`.env` you keep out of git).

```
CAPITAL_API_KEY=...
CAPITAL_EMAIL=...
CAPITAL_PASSWORD=...
CAPITAL_BASE_URL=https://demo-api-capital.backend-capital.com/api/v1
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

## Tests

```
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

## Before real money

Paper-trade the first 30 suggestions, log them, and go live only if
profitable, at 2% position size.
