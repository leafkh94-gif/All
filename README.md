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

### Deploying on Render (always-on host)

1. Push this repo to GitHub (done).
2. In Render: **New → Background Worker → connect this repo.** Render reads
   `render.yaml` automatically.
3. Set the environment variables when prompted:
   `CAPITAL_API_KEY`, `CAPITAL_EMAIL`, `CAPITAL_PASSWORD`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   (and switch `CAPITAL_BASE_URL` to the live URL when you go off demo).
4. Deploy. The bot messages you "🤖 Bot online" when it starts.

Any always-on host works the same way (Railway, a VPS with
`systemd`/`tmux`, etc.) — the only requirement is that `run_forever.py`
stays running.

### Why not GitHub Actions?

The `market-expert-bot` workflow can still be triggered manually
(**Actions → market-expert-bot → Run workflow**) for a one-off scan, but
GitHub's `*/15` cron is throttled to fire only every 2–3 hours on free
repos and each job exits after one pass — it cannot hold a 15-minute
cadence or answer Telegram commands. Do not rely on it for live alerts.

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
