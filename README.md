# Trading Alert Bot

Alert-only trading bot for US500 / US100 / US30 / BTCUSD (Capital.com CFDs).
Scans every 15 minutes, scores setups on a 100-point engine, and sends
WATCH ⚡ / A+ 🟢 alerts to Telegram. **It suggests. It never executes trades.**

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
before exiting. One-time setup:

1. **Create a PAT so jobs can chain themselves.** GitHub profile →
   Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token. Repository access: only this
   repo. Permissions: **Actions → Read and write**. Long expiration.
2. **Add the secrets.** Repo → Settings → Secrets and variables →
   Actions → New repository secret, one per name:
   `WORKFLOW_PAT` (the token from step 1), `CAPITAL_API_KEY`,
   `CAPITAL_EMAIL`, `CAPITAL_PASSWORD`, `TELEGRAM_BOT_TOKEN`,
   `TELEGRAM_CHAT_ID`.
3. **Start the chain once:** Actions tab → market-expert-bot →
   Run workflow.

Known trade-offs of relay mode:

- a ~1–3 min blind spot every ~5h40m while jobs hand off (longer if
  GitHub's queue is slow); the hourly cron restarts the chain if it
  ever breaks
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
