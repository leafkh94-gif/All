# Gold Alert Bot — Golden Trio + SMC

Alert-only trading bot for **XAUUSD (Gold)** on Capital.com CFDs. It sends
WATCH ⚡ / A+ 🟢 alerts to Telegram. **It suggests. It never executes trades.**

## Architecture

```
                     XAUUSD
                        │
                 ┌──────┴──────┐
           Golden Trio        SMC
                 │             │
                 └──────┬──────┘
                        │
                   M15 SETUP          ← creates the opportunity
                        │
              ┌─────────┼─────────┐
             M5         H1        H4   ← modify confidence
         confirmation  context   regime
              └─────────┼─────────┘
                        │
                       M1             ← entry timing only
                        │
                      SCORE
                        │
              ┌─────────┴─────────┐
            WATCH                 A+
```

**M15 creates the opportunity. The other timeframes modify confidence;
none of them can veto it.**

### The two detectors

1. **Golden Trio** — RSI(14) momentum evidence plus 10-bar Turtle
   (Donchian) location evidence, with ZLSMA(30) slope as a separate
   signed trend-alignment axis. RSI and Turtle are *quality inputs*, not
   permission gates: there is no minimum RSI hook, no absolute level to
   cross, and no oversold veto. ZLSMA is 30 not 50 because the
   Capital.com M15 API caps at ~80 bars per request.
2. **Smart Money Concepts** — Order Block, Change of Character (CHOCH),
   and Liquidity Sweep detection via the `smartmoneyconcepts` library.

Each scan runs both and keeps the higher-quality candidate. Both report
quality on the **same 0..1 axis** against the same point budget, and both
are measured on the same ZLSMA and chop axes — so the comparison between
them is structural rather than an arbitrary rescale of two unrelated
point totals.

### Scoring

| Group | Components | Range |
|---|---|---|
| Setup (M15) | `setup_quality × SCORE_SETUP_MAX` | 0 … +45 |
| | ZLSMA / structure alignment | −12 … +15 |
| MTF | H4 regime | −12 … +12 |
| | H1 context (range location + EMA slope) | −10 … +10 |
| | M5 confirmation (displacement, RSI slope, participation) | −10 … +10 |
| | M1 entry timing (pressure, chase penalty) | −5 … +5 |
| Context | round number / chop / ATR regime | −20 … +5 |

Tiers are a **pure function of the score**: WATCH at ≥ 45, A+ at ≥ 70.
There are no post-score vetoes — an unfavourable H4 regime, a flat or
opposing ZLSMA and a chop regime all cost points, so the system can never
say "this is a 75-point setup" and then refuse to call it A+.

Each MTF contribution is **zero-centred**: a timeframe that is neutral
and a timeframe whose candles are unavailable both contribute exactly 0.
That is what lets an M15-only backtest and a full-MTF live run sit on the
same score scale instead of differing by a constant offset.

**Targets.** Two selectable ladders, both 1R / 2R / 4R:
- `TARGET_MODE = "FIXED"` (default): $25 stop, $25 TP1, $50 TP2, $100 TP3.
- `TARGET_MODE = "ATR"`: the same ladder sized off M15 ATR, clamped to
  $12–$45.

They exist as a matched pair so the question "is a fixed $25 stop right
across every volatility regime?" can be settled by comparison rather than
assumption — run the backtest both ways and diff the expectancy.

Max spread accepted per signal: $1.50 (~6% of a $25 stop).

## Validation status

**This is a rule-based prototype, not a validated strategy.** The
repository contains the machinery to measure an edge; it does not contain
a demonstrated one, and nothing here should be read as a win-rate or
profitability claim. Specifically:

- The backtester needs an external historical candle CSV; no large-sample
  result is committed here.
- The score thresholds (45 / 70) are structural defaults. Whether a score
  of X corresponds to any particular expectancy is an open empirical
  question — `tools/calibrate_scores.py` is what answers it.
- The two detectors share a score axis. Whether they share a *meaning* is
  measured, not assumed.

### Backtesting

```bash
python backtest.py --candles XAUUSD_M15.csv --json signals.json
python backtest.py --candles XAUUSD_M15.csv --m5 M5.csv --m1 M1.csv
python backtest.py --candles XAUUSD_M15.csv --target-mode ATR
```

The backtester replays the live code paths (same detectors, same scorer,
same MTF layer, same cooldown logic) so live and backtest cannot drift.
H1/H4 are aggregated from M15 with the trailing partial bar dropped, so
no unclosed higher-timeframe candle is ever visible. M5 and M1 are *not*
synthesised from M15 — pass real CSVs to exercise those layers, or they
score neutral.

It reports **two different rates**, because this is an alert bot and not
a one-position execution engine:

- **Signal opportunity rate** — every setup that cleared the score
  threshold. This measures the strategy.
- **Tradeable alert rate** — what survived the cooldown and
  one-position-at-a-time gates. This measures what you would have
  received.

Conflating them lets a position gate silently shrink the sample. Both are
simulated under three cost regimes (ideal / $0.75 / $1.50 spread) with
partial exits weighted 50% at TP1, 30% at TP2, 20% at TP3 — which is why
**expectancy, not win rate, is the number that matters**: a high TP1 hit
rate is compatible with mediocre expectancy.

### Score calibration

```bash
python tools/calibrate_scores.py --signals signals.json --oos-split 0.7
```

Reports realized expectancy per score bucket with error bars, the same
table split by detector, an in-sample/out-of-sample walk-forward split,
and the marginal expectancy of each score component. It flags
under-powered buckets and refuses to call a score informative on a sample
too small to say so.

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
